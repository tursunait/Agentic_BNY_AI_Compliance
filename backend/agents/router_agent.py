"""
Router Agent — full pipeline integration.

Combines logic from:
  router_agent/supabase_rest.py  — Supabase REST access for report_types / required_fields
  router_agent/kb_client.py      — KB existence checks, schema & required-field fetching
  router_agent/schema_validator.py — JSON-schema required-path extraction & missing-field detection
  router_agent/agent.py          — CrewAI classification agent
  router_agent/run.py            — Full run_router() orchestration with RouterResult
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests as _requests
from crewai import Agent, Crew, LLM, Process, Task
from loguru import logger

from backend.config.settings import settings
from backend.tools.field_mapper import determine_report_types, normalize_case_data

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ROUTER_LLM_MODEL = "gpt-4.1-mini"


# ---------------------------------------------------------------------------
# RouterResult dataclass
# ---------------------------------------------------------------------------

@dataclass
class RouterResult:
    report_type: str
    kb_status: str          # "EXISTS" | "MISSING"
    validated_input: Dict[str, Any]
    missing_fields: List[str] = field(default_factory=list)
    missing_field_prompts: List[Dict[str, Any]] = field(default_factory=list)
    message: str = ""
    confidence_score: float = 0.0
    reasoning: str = ""
    report_types: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_type": self.report_type,
            "report_types": self.report_types,
            "kb_status": self.kb_status,
            "validated_input": self.validated_input,
            "missing_fields": self.missing_fields,
            "missing_field_prompts": self.missing_field_prompts,
            "message": self.message,
            "confidence_score": self.confidence_score,
            "reasoning": self.reasoning,
        }


# ===========================================================================
# SUPABASE REST CLIENT
# (from router_agent/supabase_rest.py)
# ===========================================================================

_REST_DISABLED_REASON: Optional[str] = None
_SUPABASE_TIMEOUT = 5


def _disable_rest(reason: str) -> None:
    global _REST_DISABLED_REASON
    if _REST_DISABLED_REASON is None:
        _REST_DISABLED_REASON = reason
        logger.warning("Supabase REST disabled for this process: {}", reason)


def _rest_enabled() -> bool:
    if _REST_DISABLED_REASON:
        return False
    url = (settings.get_supabase_rest_url() or "").strip()
    key = (getattr(settings, "SUPABASE_ANON_KEY", None) or "").strip()
    return bool(url.startswith(("http://", "https://")) and key)


def _is_dns_error(exc: Exception) -> bool:
    markers = ("NameResolutionError", "Failed to resolve", "nodename nor servname",
                "Temporary failure in name resolution", "socket.gaierror")
    return any(m in str(exc) for m in markers)


def _rest_headers() -> Dict[str, str]:
    key = (getattr(settings, "SUPABASE_ANON_KEY", None) or "").strip()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _rest_get(url: str, params: Dict[str, str]) -> List[Dict[str, Any]]:
    try:
        r = _requests.get(url, headers=_rest_headers(), params=params, timeout=_SUPABASE_TIMEOUT)
        r.raise_for_status()
        payload = r.json()
        return payload if isinstance(payload, list) else []
    except _requests.RequestException as exc:
        if _is_dns_error(exc):
            _disable_rest(f"dns_unavailable: {exc}")
        raise


def _fetch_report_type_row(report_type_code: str) -> Optional[Dict[str, Any]]:
    if not _rest_enabled():
        return None
    code = str(report_type_code).strip().upper()
    base = settings.get_supabase_rest_url().rstrip("/")
    full_url = f"{base}/rest/v1/report_types"
    attempts = [
        {"select": "id,report_type_code,json_schema,narrative_required,is_active",
         "report_type_code": f"eq.{code}", "limit": "1"},
        {"select": "id,report_type,json_schema,narrative_required,is_active",
         "report_type": f"eq.{code}", "limit": "1"},
    ]
    for params in attempts:
        try:
            rows = _rest_get(full_url, params)
            if rows:
                return rows[0]
        except _requests.HTTPError as exc:
            if getattr(exc.response, "status_code", None) == 400:
                continue
            break
        except Exception:
            break
    return None


def _fetch_required_fields_rest(report_type_code: str, required_only: bool = True) -> List[Dict[str, Any]]:
    if not _rest_enabled():
        return []
    code = str(report_type_code).strip().upper()
    base = settings.get_supabase_rest_url().rstrip("/")
    full_url = f"{base}/rest/v1/required_fields"
    attempts = [
        {"select": "id,report_type_code,field_label,is_required,input_key,ask_user_prompt",
         "report_type_code": f"eq.{code}", "order": "id.asc"},
        {"select": "id,report_type,field_label,is_required,input_key,ask_user_prompt",
         "report_type": f"eq.{code}", "order": "id.asc"},
    ]
    if required_only:
        for p in attempts:
            p["is_required"] = "eq.true"
    for params in attempts:
        try:
            rows = _rest_get(full_url, params)
            return rows
        except _requests.HTTPError as exc:
            if getattr(exc.response, "status_code", None) == 400:
                continue
            break
        except Exception:
            break
    return []


# ===========================================================================
# SCHEMA VALIDATOR
# (from router_agent/schema_validator.py)
# ===========================================================================

def _extract_required_paths(
    node: Any, prefix: str, definitions: Dict[str, Any], visited: set
) -> List[str]:
    if not isinstance(node, dict):
        return []
    out: List[str] = []
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/definitions/"):
        key = ref.split("/")[-1]
        if key not in visited:
            visited.add(key)
            out.extend(_extract_required_paths(definitions.get(key, {}), prefix, definitions, visited))
        return out
    if node.get("type") == "array":
        out.extend(_extract_required_paths(node.get("items"), prefix, definitions, visited))
        return out
    properties = node.get("properties")
    required = node.get("required")
    if isinstance(properties, dict):
        if isinstance(required, list):
            for f in required:
                if not isinstance(f, str):
                    continue
                path = f"{prefix}.{f}" if prefix else f
                out.append(path)
                out.extend(_extract_required_paths(properties.get(f), path, definitions, visited))
        for f, child in properties.items():
            cp = f"{prefix}.{f}" if prefix else f
            out.extend(_extract_required_paths(child, cp, definitions, visited))
    return out


def _schema_required_paths(schema: Dict[str, Any], report_type: str) -> List[str]:
    direct = schema.get("required_fields")
    if isinstance(direct, list) and direct:
        return [str(x).strip() for x in direct if str(x).strip()]
    definitions = schema.get("definitions") or {}
    root = schema.get("input_payload_schema") or schema
    paths = _extract_required_paths(root, "", definitions, set())
    deduped = sorted({p for p in paths if p})
    if deduped:
        return deduped
    rt = (report_type or "").upper()
    if rt == "SAR":
        return ["case_id", "subject", "subject.name", "SuspiciousActivityInformation", "transactions"]
    if rt == "CTR":
        return ["report_type", "case_id", "subject", "subject.name", "institution", "institution.name"]
    return []


def _get_value(data: Dict[str, Any], path: str) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def get_missing_required_fields(data: Dict[str, Any], required_paths: List[str]) -> List[str]:
    return [p for p in required_paths if _is_empty(_get_value(data, p))]


def normalize_input_to_single_case(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, list) and payload:
        item = payload[0]
        return item if isinstance(item, dict) else {}
    return payload if isinstance(payload, dict) else {}


# ===========================================================================
# KB CLIENT
# (from router_agent/kb_client.py)
# ===========================================================================

def report_type_exists(report_type: str) -> bool:
    if not (report_type and str(report_type).strip()):
        return False
    rt = str(report_type).strip().upper()
    if _rest_enabled():
        row = _fetch_report_type_row(rt)
        if row is not None:
            return row.get("is_active") is not False
    try:
        from backend.knowledge_base.supabase_client import SupabaseClient
        schema = SupabaseClient().get_schema(rt)
        return schema is not None
    except Exception:
        return False


def get_report_schema(report_type: str) -> Dict[str, Any]:
    rt = str(report_type).strip().upper()
    if _rest_enabled():
        row = _fetch_report_type_row(rt)
        if row:
            raw = row.get("json_schema")
            if isinstance(raw, dict) and raw:
                return raw
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    pass
    try:
        from backend.knowledge_base.supabase_client import SupabaseClient
        schema = SupabaseClient().get_schema(rt)
        if isinstance(schema, dict):
            return schema
    except Exception:
        pass
    return {}


def get_required_field_paths(report_type: str) -> List[str]:
    rt = str(report_type).strip().upper()
    if _rest_enabled():
        rows = _fetch_required_fields_rest(rt, required_only=True)
        keys = [str(r["input_key"]).strip() for r in rows if isinstance(r, dict) and r.get("input_key")]
        if keys:
            return sorted(set(keys))
    schema = get_report_schema(rt)
    return _schema_required_paths(schema, rt) if schema else []


def get_required_fields_with_prompts(report_type: str) -> List[Dict[str, Any]]:
    rt = str(report_type).strip().upper()
    if _rest_enabled():
        rows = _fetch_required_fields_rest(rt, required_only=True)
        out = []
        for r in rows:
            if isinstance(r, dict) and r.get("input_key"):
                out.append({
                    "input_key": str(r["input_key"]).strip(),
                    "ask_user_prompt": str(r.get("ask_user_prompt") or r.get("field_label") or r["input_key"]).strip(),
                    "field_label": str(r.get("field_label") or r["input_key"]).strip(),
                })
        if out:
            return out
    return [
        {"input_key": p, "ask_user_prompt": f"Please provide value for {p}", "field_label": p}
        for p in get_required_field_paths(rt)
    ]


# ===========================================================================
# HELPER UTILITIES
# (from router_agent/run.py helpers)
# ===========================================================================

def _first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _safe_set_nested(payload: Dict[str, Any], path: str, value: Any) -> None:
    """Set a dot-path key only when the leaf is currently empty."""
    if value is None or str(value).strip() == "":
        return
    parts = [p for p in path.split(".") if p]
    if not parts:
        return
    current = payload
    for key in parts[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    leaf = parts[-1]
    existing = current.get(leaf)
    if existing is None or (isinstance(existing, str) and not existing.strip()):
        current[leaf] = value


def _first_tx(case: Dict[str, Any]) -> Dict[str, Any]:
    txs = case.get("transactions")
    if isinstance(txs, list):
        for tx in txs:
            if isinstance(tx, dict):
                return tx
    return {}


def _to_mmddyyyy(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    probe = text[:10]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y/%m/%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(probe, fmt).strftime("%m/%d/%Y")
        except Exception:
            continue
    return probe


def _derive_total_amount(case: Dict[str, Any]) -> float:
    sai = case.get("SuspiciousActivityInformation") if isinstance(case.get("SuspiciousActivityInformation"), dict) else {}
    amount_block = sai.get("26_AmountInvolved") if isinstance(sai.get("26_AmountInvolved"), dict) else {}
    part2 = case.get("part_2_suspicious_activity") if isinstance(case.get("part_2_suspicious_activity"), dict) else {}
    findings = (case.get("investigation_details", {}).get("findings", {})
                if isinstance(case.get("investigation_details"), dict) else {})
    beneficiary = findings.get("beneficiary_analysis") if isinstance(findings.get("beneficiary_analysis"), dict) else {}
    for candidate in (
        amount_block.get("amount_usd"), case.get("total_amount_involved"), case.get("amount"),
        sai.get("28_CumulativeAmount"), part2.get("amount_involved"), beneficiary.get("financial_benefit"),
    ):
        try:
            v = float(str(candidate).replace(",", "").replace("$", "").strip())
            if v > 0:
                return v
        except Exception:
            pass
    total = 0.0
    txs = case.get("transactions")
    if isinstance(txs, list):
        for tx in txs:
            if not isinstance(tx, dict):
                continue
            for key in ("amount_usd", "amount"):
                try:
                    amount = float(str(tx.get(key) or 0).replace(",", "").replace("$", "").strip() or 0)
                    if amount:
                        total += amount
                        break
                except Exception:
                    continue
    return total


def _derive_suspicious_activity_dates(case: Dict[str, Any]) -> tuple[str, str]:
    sai = case.get("SuspiciousActivityInformation") if isinstance(case.get("SuspiciousActivityInformation"), dict) else {}
    date_block = sai.get("27_DateOrDateRange") if isinstance(sai.get("27_DateOrDateRange"), dict) else {}
    part2 = case.get("part_2_suspicious_activity") if isinstance(case.get("part_2_suspicious_activity"), dict) else {}
    period = part2.get("activity_period") if isinstance(part2.get("activity_period"), dict) else {}
    start = _to_mmddyyyy(_first_non_empty(date_block.get("from"), date_block.get("start"),
                                          period.get("from_date"), period.get("from")))
    end = _to_mmddyyyy(_first_non_empty(date_block.get("to"), date_block.get("end"),
                                        period.get("to_date"), period.get("to")))
    tx_dates: list[str] = []
    txs = case.get("transactions")
    if isinstance(txs, list):
        for tx in txs:
            if not isinstance(tx, dict):
                continue
            raw = _first_non_empty(tx.get("date"), tx.get("timestamp"), tx.get("datetime"))
            normalized = _to_mmddyyyy(raw)
            if normalized:
                tx_dates.append(normalized)
    if tx_dates:
        parsed: list[tuple[datetime, str]] = []
        for val in tx_dates:
            try:
                parsed.append((datetime.strptime(val, "%m/%d/%Y"), val))
            except Exception:
                continue
        if parsed:
            parsed.sort(key=lambda item: item[0])
            if not start:
                start = parsed[0][1]
            if not end:
                end = parsed[-1][1]
    return start, end


def _derive_suspicious_activity_date_range_text(case: Dict[str, Any]) -> str:
    start, end = _derive_suspicious_activity_dates(case)
    if start and end:
        return f"{start} to {end}" if start != end else start
    return start or end


# ===========================================================================
# CASE SHAPE NORMALISATION
# ===========================================================================

def _map_alternate_case_shapes(case: Dict[str, Any]) -> None:
    """Map part_1/3/4 and report_metadata into canonical keys."""
    report_meta = case.get("report_metadata") if isinstance(case.get("report_metadata"), dict) else {}
    if report_meta:
        filing_date = _to_mmddyyyy(_first_non_empty(report_meta.get("filing_date")))
        _safe_set_nested(case, "filing_type", _first_non_empty(report_meta.get("report_type"), case.get("filing_type"), "initial"))
        _safe_set_nested(case, "report_type", _first_non_empty(report_meta.get("filing_type"), case.get("report_type")))
        _safe_set_nested(case, "sar_filing_date", filing_date)

    part1 = case.get("part_1_subject_information") if isinstance(case.get("part_1_subject_information"), dict) else {}
    if part1:
        personal = part1.get("personal_details") if isinstance(part1.get("personal_details"), dict) else {}
        address = part1.get("address") if isinstance(part1.get("address"), dict) else {}
        ident = part1.get("identification") if isinstance(part1.get("identification"), dict) else {}
        first = _first_non_empty(personal.get("first_name"))
        last = _first_non_empty(personal.get("last_name"))
        full_name = _first_non_empty(
            case.get("subject", {}).get("name") if isinstance(case.get("subject"), dict) else None,
            f"{first} {last}".strip(),
        )
        _safe_set_nested(case, "subject.first_name", first)
        _safe_set_nested(case, "subject.last_name", last)
        _safe_set_nested(case, "subject.name", full_name)
        _safe_set_nested(case, "subject.city", address.get("city"))
        _safe_set_nested(case, "subject.state", address.get("state"))
        _safe_set_nested(case, "subject.zip", address.get("zip_code"))
        _safe_set_nested(case, "subject.address", address.get("street"))
        _safe_set_nested(case, "subject.country", address.get("country_code"))
        _safe_set_nested(case, "subject.tin", ident.get("tin"))
        _safe_set_nested(case, "subject.ssn_or_ein", ident.get("tin"))
        _safe_set_nested(case, "subject.id_type", ident.get("id_form"))
        dob = _to_mmddyyyy(_first_non_empty(personal.get("date_of_birth")))
        _safe_set_nested(case, "subject.dob", dob)
        _safe_set_nested(case, "subject.date_of_birth", dob)
        _safe_set_nested(case, "subject.occupation", personal.get("occupation"))

    part3 = case.get("part_3_institution_where_occurred") if isinstance(case.get("part_3_institution_where_occurred"), dict) else {}
    if part3:
        details = part3.get("institution_details") if isinstance(part3.get("institution_details"), dict) else {}
        branch = part3.get("branch_information") if isinstance(part3.get("branch_information"), dict) else {}
        regulator = _first_non_empty(part3.get("primary_federal_regulator"))
        inst_type = _first_non_empty(part3.get("institution_type"))
        for prefix in ("institution", "financial_institution"):
            _safe_set_nested(case, f"{prefix}.name", details.get("legal_name"))
            _safe_set_nested(case, f"{prefix}.ein", details.get("tin"))
            _safe_set_nested(case, f"{prefix}.tin", details.get("tin"))
            _safe_set_nested(case, f"{prefix}.primary_federal_regulator", regulator)
            _safe_set_nested(case, f"{prefix}.federal_regulator", regulator)
            _safe_set_nested(case, f"{prefix}.type", inst_type)
            _safe_set_nested(case, f"{prefix}.address", branch.get("branch_address"))
            _safe_set_nested(case, f"{prefix}.city", branch.get("branch_city"))
            _safe_set_nested(case, f"{prefix}.branch_city", branch.get("branch_city"))
            _safe_set_nested(case, f"{prefix}.state", branch.get("branch_state"))
            _safe_set_nested(case, f"{prefix}.branch_state", branch.get("branch_state"))
            _safe_set_nested(case, f"{prefix}.zip", branch.get("branch_zip"))
            _safe_set_nested(case, f"{prefix}.country", branch.get("branch_country"))
        _safe_set_nested(case, "financial_institution.ein_or_ssn", details.get("tin"))

    part4 = case.get("part_4_filing_institution") if isinstance(case.get("part_4_filing_institution"), dict) else {}
    if part4:
        filer = part4.get("filer_details") if isinstance(part4.get("filer_details"), dict) else {}
        addr = part4.get("address") if isinstance(part4.get("address"), dict) else {}
        regulator = _first_non_empty(part4.get("primary_federal_regulator"))
        inst_type = _first_non_empty(part4.get("institution_type"))
        filing_date = _to_mmddyyyy(_first_non_empty(part4.get("filing_date"), report_meta.get("filing_date")))
        _safe_set_nested(case, "filing_institution.name", filer.get("legal_name"))
        _safe_set_nested(case, "filing_institution.tin", filer.get("tin"))
        _safe_set_nested(case, "filing_institution.address", addr.get("street"))
        _safe_set_nested(case, "filing_institution.city", addr.get("city"))
        _safe_set_nested(case, "filing_institution.state", addr.get("state"))
        _safe_set_nested(case, "filing_institution.zip", addr.get("zip_code"))
        _safe_set_nested(case, "filing_institution.country", addr.get("country_code"))
        _safe_set_nested(case, "filing_institution.federal_regulator", regulator)
        _safe_set_nested(case, "filing_institution.primary_federal_regulator", regulator)
        _safe_set_nested(case, "filing_institution.type", inst_type)
        _safe_set_nested(case, "filing_institution.contact_office", part4.get("contact_office"))
        _safe_set_nested(case, "filing_institution.contact_phone", part4.get("contact_phone"))
        _safe_set_nested(case, "filing_institution.date_filed", filing_date)

    date_range_text = _derive_suspicious_activity_date_range_text(case)
    if date_range_text:
        _safe_set_nested(case, "activity_date_range.start", date_range_text.split(" to ")[0])
        _safe_set_nested(case, "activity_date_range.end", date_range_text.split(" to ")[-1])
    total_amount = _derive_total_amount(case)
    if total_amount > 0:
        _safe_set_nested(case, "amount", total_amount)
        _safe_set_nested(case, "total_amount_involved", total_amount)


def _apply_static_filing_institution_defaults(case: Dict[str, Any]) -> None:
    """Fill BNY Mellon institution defaults so downstream agents never need to ask."""
    today = datetime.now().strftime("%m/%d/%Y")
    statics = {
        "filing_institution.address": "2334 Park Ave",
        "filing_institution.street_address": "2334 Park Ave",
        "filing_institution.city": "New York",
        "filing_institution.contact_office": "Compliance",
        "filing_institution.contact_department": "Compliance",
        "filing_institution.contact_phone": "6502798978",
        "filing_institution.country": "US",
        "filing_institution.country_code": "US",
        "filing_institution.primary_federal_regulator": "BSA",
        "filing_institution.name": "BNY Mellon",
        "filing_institution.tin": "135266003",
        "filing_institution.tin_type": "EIN",
        "filing_institution.institution_type": "Bank",
        "filing_institution.zip": "10286",
        "filing_institution.date_filed": today,
        "sar_filing_date": today,
        "institution.address": "2334 Park Ave",
        "institution.branch_city": "New York",
        "institution.city": "New York",
        "institution.contact_officer": "Compliance",
        "institution.contact_phone": "6502798978",
        "institution.country": "US",
        "institution.primary_federal_regulator": "BSA",
        "institution.name": "BNY Mellon",
        "institution.ein": "135266003",
        "institution.tin": "135266003",
        "institution.type": "Bank",
        "institution.zip": "10286",
        "financial_institution.address": "2334 Park Ave",
        "financial_institution.city": "New York",
        "financial_institution.state": "NY",
        "financial_institution.zip": "10286",
        "financial_institution.name": "BNY Mellon",
        "financial_institution.ein_or_ssn": "135266003",
        "financial_institution.primary_federal_regulator": "BSA",
    }
    for path, value in statics.items():
        _safe_set_nested(case, path, value)


# ===========================================================================
# AUTOFILL & MISSING FIELD FILTERING
# (from router_agent/run.py)
# ===========================================================================

def _autofill_known_prompt_keys(case: Dict[str, Any], required_paths: List[str]) -> None:
    """Autofill values when required paths are stored as human prompt text."""
    total_amount = _derive_total_amount(case)
    today = datetime.now().strftime("%m/%d/%Y")
    date_range_text = _derive_suspicious_activity_date_range_text(case)
    for raw_path in required_paths:
        path = str(raw_path or "").strip()
        if not path:
            continue
        low = path.lower()
        if total_amount > 0 and "amount" in low and ("dollar" in low or "suspicious" in low or "involved" in low):
            _safe_set_nested(case, path, f"{total_amount:.2f}")
            continue
        if date_range_text and ("date range" in low or "date or date range" in low) and ("suspicious activity" in low or "activity period" in low):
            _safe_set_nested(case, path, date_range_text)
            continue
        if "date" in low and ("sar" in low or "filed" in low or "filing" in low):
            _safe_set_nested(case, path, today)
            continue
        if "filing institution" in low or "institution filing this sar" in low:
            if "street" in low or "address" in low:
                _safe_set_nested(case, path, "2334 Park Ave")
            elif "city" in low:
                _safe_set_nested(case, path, "New York")
            elif "contact office" in low or "department" in low:
                _safe_set_nested(case, path, "Compliance")
            elif "phone" in low:
                _safe_set_nested(case, path, "6502798978")
            elif "country" in low:
                _safe_set_nested(case, path, "US")
            elif "regulator" in low:
                _safe_set_nested(case, path, "BSA")
            elif "name" in low:
                _safe_set_nested(case, path, "BNY Mellon")
            elif "tin" in low:
                _safe_set_nested(case, path, "135266003")
            elif "institution type" in low or "type of institution" in low:
                _safe_set_nested(case, path, "Bank")
            elif "zip" in low:
                _safe_set_nested(case, path, "10286")


def _is_redundant_activity_date_range_path(path: str) -> bool:
    low = str(path or "").strip().lower()
    if not low:
        return False
    if "27_dateordaterange" in low or "activity_date_range" in low:
        return True
    return ("date range" in low or "date or date range" in low) and ("suspicious activity" in low or "activity period" in low)


def _is_redundant_total_amount_path(path: str) -> bool:
    low = str(path or "").strip().lower()
    if not low:
        return False
    if "total_amount_involved" in low or "26_amountinvolved" in low or "28_cumulativeamount" in low:
        return True
    return "total dollar amount" in low and "suspicious" in low and "activity" in low


def _is_auto_derived_sar_filing_date_path(path: str) -> bool:
    low = str(path or "").strip().lower()
    if not low:
        return False
    if low in {"sar_filing_date", "filing_institution.date_filed", "filing_date", "date_filed"}:
        return True
    if "date this sar is being filed" in low:
        return True
    return "sar" in low and "date" in low and ("filed" in low or "filing" in low)


def _drop_auto_derived_missing_fields(case: Dict[str, Any], missing_fields: List[str]) -> List[str]:
    """Remove from missing_fields anything that can be deterministically derived."""
    total_amount = _derive_total_amount(case)
    today = datetime.now().strftime("%m/%d/%Y")
    date_range_text = _derive_suspicious_activity_date_range_text(case)
    filtered: List[str] = []
    for path in missing_fields:
        if _is_auto_derived_sar_filing_date_path(path):
            _safe_set_nested(case, path, today)
            continue
        if date_range_text and _is_redundant_activity_date_range_path(path):
            _safe_set_nested(case, path, date_range_text)
            continue
        if total_amount > 0 and _is_redundant_total_amount_path(path):
            _safe_set_nested(case, path, f"{total_amount:.2f}")
            continue
        filtered.append(path)
    return filtered


# ===========================================================================
# SEMANTIC MISSING FIELD RESOLUTION
# (from router_agent/run.py)
# ===========================================================================

def _value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


def _get_nested_value(payload: Dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in str(path or "").split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _collect_non_empty_paths(value: Any, prefix: str = "", out: Dict[str, str] | None = None) -> Dict[str, str]:
    if out is None:
        out = {}
    if isinstance(value, dict):
        for key, item in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            _collect_non_empty_paths(item, child_prefix, out)
        return out
    if isinstance(value, list):
        if value and prefix:
            out[prefix] = f"list[{len(value)}]"
        return out
    if _value_present(value) and prefix:
        out[prefix] = str(value)[:120]
    return out


def _known_semantic_match(case: Dict[str, Any], missing_path: str, report_type: str) -> bool:
    """Deterministically check if a missing field is already covered by equivalent data."""
    low = str(missing_path or "").strip().lower()
    if not low:
        return False
    if _is_redundant_activity_date_range_path(missing_path):
        return bool(_derive_suspicious_activity_date_range_text(case))
    if _is_redundant_total_amount_path(missing_path):
        return _derive_total_amount(case) > 0
    if ("primary federal regulator of the filing institution" in low
            or ("filing institution" in low and "regulator" in low)
            or low in {"filing_institution.federal_regulator", "filing_institution.primary_federal_regulator"}):
        for candidate in ("filing_institution.primary_federal_regulator", "filing_institution.federal_regulator",
                          "institution.primary_federal_regulator", "financial_institution.primary_federal_regulator"):
            if _value_present(_get_nested_value(case, candidate)):
                return True
        return False
    if "filing institution" in low and "city" in low:
        return _value_present(_get_nested_value(case, "filing_institution.city"))
    if "filing institution" in low and ("address" in low or "street" in low):
        return _value_present(_get_nested_value(case, "filing_institution.address"))
    if "filing institution" in low and "phone" in low:
        return _value_present(_get_nested_value(case, "filing_institution.contact_phone"))
    if "filing institution" in low and "name" in low:
        return _value_present(_get_nested_value(case, "filing_institution.name"))
    if "filing institution" in low and "zip" in low:
        return _value_present(_get_nested_value(case, "filing_institution.zip"))
    if "filing institution" in low and "country" in low:
        return _value_present(_get_nested_value(case, "filing_institution.country"))
    if low in {"activity_date_range", "activity_date_range.start", "activity_date_range.end"}:
        return (_value_present(_get_nested_value(case, "activity_date_range.start"))
                and _value_present(_get_nested_value(case, "activity_date_range.end")))
    return False


def _llm_semantic_resolve(case: Dict[str, Any], missing_fields: List[str], report_type: str) -> set[str]:
    """Use LLM to identify which missing_fields are already satisfied by semantically equivalent populated fields."""
    api_key = str(getattr(settings, "OPENAI_API_KEY", "") or "").strip()
    if not api_key or not missing_fields:
        return set()
    candidate_map = _collect_non_empty_paths(case)
    if not candidate_map:
        return set()
    base_url = str(getattr(settings, "OPENAI_BASE_URL", "") or "https://api.openai.com/v1").rstrip("/")
    model = str(getattr(settings, "OPENAI_MODEL", "") or "gpt-4o-mini").strip()
    try:
        resp = _requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": (
                        "You map required schema labels to equivalent populated fields. "
                        "Return only JSON with key resolved_labels (array of missing labels already present by equivalent meaning)."
                    )},
                    {"role": "user", "content": json.dumps({
                        "report_type": report_type,
                        "missing_fields": missing_fields[:8],
                        "candidate_fields": candidate_map,
                    }, ensure_ascii=False)},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=6,
        )
        resp.raise_for_status()
        content = (((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        parsed = json.loads(content) if content else {}
        resolved = parsed.get("resolved_labels") if isinstance(parsed, dict) else []
        return {str(item) for item in (resolved or []) if str(item).strip()}
    except Exception as exc:
        logger.debug("LLM semantic resolver skipped: {}", exc)
        return set()


def _semantic_missing_field_filter(
    case: Dict[str, Any], missing_fields: List[str], report_type: str
) -> List[str]:
    """Combine deterministic + LLM semantic checks to remove fields that are already covered."""
    if not missing_fields:
        return []
    unresolved = [f for f in missing_fields if not _known_semantic_match(case, f, report_type)]
    if not unresolved:
        return []
    resolved_by_llm = _llm_semantic_resolve(case, unresolved, report_type)
    return [f for f in unresolved if f not in resolved_by_llm]


def _prompts_for_missing(missing_fields: List[str], schema_lookup_types: List[str]) -> List[Dict[str, Any]]:
    """Build ask_user_prompt entries for each missing field."""
    field_meta: Dict[str, Dict[str, Any]] = {}
    for rt in schema_lookup_types:
        for meta in get_required_fields_with_prompts(rt):
            key = str(meta.get("input_key") or "").strip()
            if key and key not in field_meta:
                field_meta[key] = meta
    out = []
    for path in missing_fields:
        meta = field_meta.get(path)
        if meta:
            out.append(meta)
        else:
            out.append({"input_key": path, "ask_user_prompt": f"Please provide value for {path}", "field_label": path})
    return out


# ===========================================================================
# FULL CASE DERIVATION (public export used by crew.py)
# ===========================================================================

def derive_and_normalize_case(case_data: Dict[str, Any], report_type: str) -> Dict[str, Any]:
    """
    Full pre-processing pass: alternate shapes → static defaults → date/amount derivation
    → subject/institution fallbacks → CTR person_a fields.
    """
    case = normalize_case_data(case_data)
    if not isinstance(case, dict):
        return {}

    _map_alternate_case_shapes(case)

    subject = case.get("subject") if isinstance(case.get("subject"), dict) else {}
    institution = case.get("institution") if isinstance(case.get("institution"), dict) else {}
    first_tx = _first_tx(case)

    location = str(first_tx.get("location") or "")
    fallback_city, fallback_state = "", ""
    if "," in location:
        city_part, state_part = location.split(",", 1)
        fallback_city = city_part.strip()
        fallback_state = state_part.strip()[:2]

    _safe_set_nested(case, "filing_type", _first_non_empty(case.get("filing_type"), "initial"))
    _apply_static_filing_institution_defaults(case)

    part2 = case.get("part_2_suspicious_activity") if isinstance(case.get("part_2_suspicious_activity"), dict) else {}
    activity_period = part2.get("activity_period") if isinstance(part2.get("activity_period"), dict) else {}
    activity_from = _to_mmddyyyy(_first_non_empty(activity_period.get("from_date"), activity_period.get("from")))
    activity_to = _to_mmddyyyy(_first_non_empty(activity_period.get("to_date"), activity_period.get("to")))
    if activity_from:
        _safe_set_nested(case, "SuspiciousActivityInformation.27_DateOrDateRange.from", activity_from)
        _safe_set_nested(case, "activity_date_range.start", activity_from)
    if activity_to:
        _safe_set_nested(case, "SuspiciousActivityInformation.27_DateOrDateRange.to", activity_to)
        _safe_set_nested(case, "activity_date_range.end", activity_to)
    if part2.get("amount_involved") not in (None, ""):
        _safe_set_nested(case, "SuspiciousActivityInformation.26_AmountInvolved.amount_usd", part2["amount_involved"])
        _safe_set_nested(case, "total_amount_involved", part2["amount_involved"])

    date_range_text = _derive_suspicious_activity_date_range_text(case)
    if date_range_text:
        _safe_set_nested(case, "What is the date range of the suspicious activity?", date_range_text)
        _safe_set_nested(case, "What is the date range of the suspicious activity", date_range_text)

    _safe_set_nested(case, "financial_institution.name", institution.get("name"))
    _safe_set_nested(case, "financial_institution.city", _first_non_empty(institution.get("branch_city"), fallback_city))
    _safe_set_nested(case, "financial_institution.state", _first_non_empty(institution.get("branch_state"), fallback_state))
    _safe_set_nested(case, "financial_institution.zip", _first_non_empty(institution.get("zip"), institution.get("postal_code"), "00000"))
    _safe_set_nested(case, "financial_institution.address", _first_non_empty(
        institution.get("address"),
        f"{_first_non_empty(institution.get('branch_city'), fallback_city)}, {_first_non_empty(institution.get('branch_state'), fallback_state)}".strip(", "),
    ))
    _safe_set_nested(case, "financial_institution.ein_or_ssn", _first_non_empty(
        institution.get("ein"), institution.get("tin"), institution.get("ein_or_ssn"), "UNKNOWN",
    ))

    _safe_set_nested(case, "subject.city", _first_non_empty(subject.get("city"), fallback_city))
    _safe_set_nested(case, "subject.state", _first_non_empty(subject.get("state"), fallback_state))
    _safe_set_nested(case, "subject.zip", _first_non_empty(subject.get("zip"), subject.get("postal_code"), "00000"))
    _safe_set_nested(case, "subject.address", _first_non_empty(
        subject.get("address"),
        f"{_first_non_empty(subject.get('city'), fallback_city)}, {_first_non_empty(subject.get('state'), fallback_state)}".strip(", "),
    ))
    _safe_set_nested(case, "subject.tin", _first_non_empty(
        subject.get("tin"), subject.get("ssn_or_ein"), subject.get("ssn"), subject.get("ein"), "UNKNOWN",
    ))
    _safe_set_nested(case, "subject.date_of_birth", _first_non_empty(
        subject.get("date_of_birth"), subject.get("dob"), subject.get("onboarding_date"), "1900-01-01",
    ))

    ts = str(first_tx.get("timestamp") or "")
    tx_date = ts.split(" ", 1)[0] if ts else str(first_tx.get("date") or "").strip()
    if not tx_date:
        dr = case.get("SuspiciousActivityInformation", {}).get("27_DateOrDateRange", {}) if isinstance(case.get("SuspiciousActivityInformation"), dict) else {}
        tx_date = str(dr.get("from") or "") if isinstance(dr, dict) else ""
    _safe_set_nested(case, "transaction.date", tx_date)

    total_amount = _derive_total_amount(case)
    if total_amount > 0:
        _safe_set_nested(case, "total_amount_involved", total_amount)
        _safe_set_nested(case, "SuspiciousActivityInformation.26_AmountInvolved.amount_usd", total_amount)
        _safe_set_nested(case, "SuspiciousActivityInformation.26_AmountInvolved.no_amount", False)
        _safe_set_nested(case, "SuspiciousActivityInformation.28_CumulativeAmount", f"{total_amount:.2f}")
        _safe_set_nested(case, "What is the total dollar amount involved in this suspicious activity?", f"{total_amount:.2f}")
        _safe_set_nested(case, "total dollar amount involved in this suspicious activity", f"{total_amount:.2f}")

    rt = str(report_type or "").strip().upper()
    if rt in {"CTR", "BOTH"}:
        full_name = str(subject.get("name") or "").strip()
        parts = [p for p in full_name.split(" ") if p]
        first_name = parts[0] if parts else "UNKNOWN"
        last_name = parts[-1] if len(parts) > 1 else (parts[0] if parts else "UNKNOWN")
        _safe_set_nested(case, "person_a.first_name", _first_non_empty(subject.get("first_name"), first_name))
        _safe_set_nested(case, "person_a.last_name", _first_non_empty(subject.get("last_name"), last_name))
        _safe_set_nested(case, "person_a.city", _first_non_empty(subject.get("city"), fallback_city))
        _safe_set_nested(case, "person_a.state", _first_non_empty(subject.get("state"), fallback_state))
        _safe_set_nested(case, "person_a.zip", _first_non_empty(subject.get("zip"), subject.get("postal_code"), "00000"))
        _safe_set_nested(case, "person_a.address", _first_non_empty(
            subject.get("address"),
            f"{_first_non_empty(subject.get('city'), fallback_city)}, {_first_non_empty(subject.get('state'), fallback_state)}".strip(", "),
        ))
        _safe_set_nested(case, "person_a.ssn_or_ein", _first_non_empty(subject.get("tin"), subject.get("ssn_or_ein"), "UNKNOWN"))

    return case


def strip_prompt_keys(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Drop top-level keys that are prompt-text artifacts before passing to downstream agents."""
    cleaned = dict(payload)
    for key in list(cleaned.keys()):
        if not isinstance(key, str):
            continue
        key_text = key.strip()
        lowered = key_text.lower()
        if not key_text:
            continue
        if "?" in key_text:
            cleaned.pop(key, None)
            continue
        if lowered.startswith(("what ", "who ", "please ", "enter ", "provide ")):
            cleaned.pop(key, None)
            continue
        if " " in key_text and "." not in key_text and len(key_text) > 30:
            cleaned.pop(key, None)
    return cleaned


def fallback_classify(user_input: Any) -> Dict[str, Any]:
    """Deterministic fallback when LLM router fails."""
    case = normalize_case_data(user_input)
    report_types = determine_report_types(case)
    if not report_types:
        has_part2 = isinstance(case.get("part_2_suspicious_activity"), dict)
        has_red_flags = isinstance(case.get("red_flags"), list) and len(case.get("red_flags") or []) > 0
        has_narrative = isinstance(case.get("narrative"), dict) and bool((case.get("narrative") or {}).get("text"))
        if has_part2 or has_red_flags or has_narrative:
            report_types = ["SAR"]
        else:
            return {"report_types": [], "report_type": "OTHER",
                    "confidence_score": 0.0, "reasoning": "No clear filing signal from structured data."}
    return {
        "report_types": report_types,
        "report_type": report_types[0],
        "confidence_score": 0.95,
        "reasoning": "Derived from transaction thresholds and suspicious-activity signals.",
    }


# ===========================================================================
# FULL run_router() ORCHESTRATION
# ===========================================================================

def run_router(
    user_input: Any,
    *,
    skip_llm_if_json_with_report_type: bool = True,
) -> RouterResult:
    """
    Full router orchestration:
    1. Classify report type (from hint or LLM)
    2. Check KB existence
    3. Derive and autofill case fields
    4. Validate against required fields, filter semantic duplicates
    5. Return RouterResult with validated_input + missing_fields + prompts
    """
    # --- 1. Get report type ---
    report_type = "SAR"
    confidence_score = 0.0
    reasoning = ""
    report_types: List[str] = []

    if isinstance(user_input, dict) and skip_llm_if_json_with_report_type:
        report_meta = user_input.get("report_metadata") if isinstance(user_input.get("report_metadata"), dict) else {}
        hint = (
            user_input.get("report_type")
            or user_input.get("report_types")
            or report_meta.get("filing_type")
            or report_meta.get("report_type")
        )
        if hint:
            if isinstance(hint, list):
                report_type = str(hint[0]).strip().upper() if hint else "SAR"
            else:
                report_type = str(hint).strip().upper()
            if report_type in ("SAR", "CTR", "SANCTIONS", "BOTH"):
                confidence_score = 1.0
                reasoning = f"Report type '{report_type}' specified directly in input."
                report_types = ["SAR", "CTR"] if report_type == "BOTH" else [report_type]
            else:
                report_type = "SAR"

    if not report_types:
        try:
            classification = classify_report_type(user_input)
            report_type = str(classification.get("report_type") or "SAR").strip().upper()
            confidence_score = float(classification.get("confidence_score") or 0.0)
            reasoning = str(classification.get("reasoning") or "")
            if report_type == "BOTH":
                report_types = ["SAR", "CTR"]
            elif report_type in ("SAR", "CTR", "SANCTIONS"):
                report_types = [report_type]
            else:
                report_type = "SAR"
                report_types = ["SAR"]
        except Exception as exc:
            logger.warning("LLM classification failed: {}", exc)
            fb = fallback_classify(user_input)
            report_types = fb.get("report_types") or ["SAR"]
            report_type = fb.get("report_type") or "SAR"
            confidence_score = float(fb.get("confidence_score") or 0.5)
            reasoning = fb.get("reasoning") or ""

    schema_lookup_types = report_types if report_types else [report_type]

    # --- 2. KB existence check ---
    kb_status = "EXISTS" if report_type_exists(report_type) else "MISSING"

    # --- 3. Get required field paths ---
    required_paths: List[str] = []
    for rt in schema_lookup_types:
        for p in get_required_field_paths(rt):
            if p not in required_paths:
                required_paths.append(p)

    # --- 4. Build structured case data ---
    if isinstance(user_input, dict):
        case_data = normalize_case_data(user_input)
    elif isinstance(user_input, list):
        case_data = normalize_input_to_single_case(user_input)
    else:
        # Free-text: extract structured fields via LLM
        narrative = str(user_input).strip()
        case_data = normalize_case_data({"case_description": narrative})
        extracted = _extract_structured_fields_from_narrative(narrative, report_type, schema_lookup_types)
        for input_key, value in extracted.items():
            if isinstance(input_key, str) and input_key.strip() and value is not None:
                _safe_set_nested(case_data, input_key.strip(), value)

    # --- 5. Derive, autofill, filter missing fields ---
    case_data = derive_and_normalize_case(case_data, report_type)
    _autofill_known_prompt_keys(case_data, required_paths)

    missing_fields = get_missing_required_fields(case_data, required_paths)
    missing_fields = _drop_auto_derived_missing_fields(case_data, missing_fields)
    missing_fields = _semantic_missing_field_filter(case_data, missing_fields, report_type)

    message = (
        f"Report type '{report_type}' validated. {len(missing_fields)} field(s) still required."
        if missing_fields
        else f"Report type '{report_type}' validated. All required fields present."
    )

    return RouterResult(
        report_type=report_type,
        report_types=report_types,
        kb_status=kb_status,
        validated_input=case_data,
        missing_fields=missing_fields,
        missing_field_prompts=_prompts_for_missing(missing_fields, schema_lookup_types),
        message=message,
        confidence_score=confidence_score,
        reasoning=reasoning,
    )


def _extract_structured_fields_from_narrative(
    narrative_text: str, report_type: str, schema_lookup_types: List[str]
) -> Dict[str, Any]:
    """Extract structured field values from free-text via LLM using Supabase required_fields metadata."""
    field_meta: Dict[str, Dict[str, Any]] = {}
    for rt in schema_lookup_types:
        for meta in get_required_fields_with_prompts(rt):
            key = str(meta.get("input_key") or "").strip()
            if key and key not in field_meta:
                field_meta[key] = meta
    if not field_meta:
        return {}
    api_key = str(getattr(settings, "OPENAI_API_KEY", "") or "").strip()
    if not api_key:
        return {}
    fields_list = [
        {"input_key": k, "field_label": m.get("field_label") or k, "ask_user_prompt": (m.get("ask_user_prompt") or "")[:200]}
        for k, m in field_meta.items()
    ]
    base_url = str(getattr(settings, "OPENAI_BASE_URL", "") or "https://api.openai.com/v1").rstrip("/")
    model = str(getattr(settings, "OPENAI_MODEL", "") or "gpt-4o-mini").strip()
    try:
        resp = _requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model, "temperature": 0,
                "messages": [
                    {"role": "system", "content": (
                        "Extract structured data from a compliance narrative. "
                        "Return JSON with keys exactly matching input_key values. "
                        "Value = what narrative states, or null if not mentioned. "
                        "Dates MM/DD/YYYY. Amounts as numbers only."
                    )},
                    {"role": "user", "content": json.dumps(
                        {"report_type": report_type, "narrative": narrative_text[:12000], "required_fields": fields_list},
                        ensure_ascii=False,
                    )},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = (((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        parsed = json.loads(content) if content else {}
        return {k: v for k, v in parsed.items() if k in field_meta and v is not None and str(v).strip()}
    except Exception as exc:
        logger.warning("LLM narrative extraction failed: {}", exc)
        return {}


# ===========================================================================
# CREWAI AGENT & TASK FACTORIES
# ===========================================================================

def classify_report_type(user_input: Any) -> Dict[str, Any]:
    """Run the CrewAI router agent to classify report type. Returns report_type, confidence_score, reasoning."""
    llm = LLM(model=ROUTER_LLM_MODEL, temperature=0.0, max_tokens=2000, api_key=settings.OPENAI_API_KEY)
    agent = create_router_agent(llm, tools=[])
    task = create_router_task(agent, user_input)
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=True)
    result = crew.kickoff()

    raw = result
    if hasattr(raw, "raw"):
        raw = getattr(raw, "raw", raw)
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:] if lines[0].startswith("```") else lines
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return {"report_type": "OTHER", "confidence_score": 0.0, "reasoning": "Could not parse agent output."}
    if not isinstance(raw, dict):
        return {"report_type": "OTHER", "confidence_score": 0.0, "reasoning": "Invalid agent output."}
    report_type = str(raw.get("report_type", "OTHER")).strip().upper()
    if report_type not in {"SAR", "CTR", "SANCTIONS", "BOTH", "OTHER"}:
        report_type = "OTHER"
    return {
        "report_type": report_type,
        "confidence_score": float(raw.get("confidence_score", 0.0)),
        "reasoning": str(raw.get("reasoning", "")),
    }


def _task_description(user_input: Any) -> str:
    if isinstance(user_input, dict):
        return f"""The user has provided structured (JSON) data. Infer the required report type.

Structured input:
{json.dumps(user_input, indent=2, default=str)}

Return a JSON object with: report_type (SAR/CTR/SANCTIONS/BOTH/OTHER), confidence_score (0-1), reasoning.
OFAC_REJECT → use SANCTIONS. Return only valid JSON."""
    return f"""The user has provided a natural language compliance request:

"{user_input}"

Determine the required report type: SAR, CTR, SANCTIONS (for OFAC rejections), or BOTH.
Return JSON: report_type, confidence_score (0-1), reasoning. No markdown."""


def create_router_agent(llm: LLM, tools: list) -> Agent:
    return Agent(
        role="Compliance Report Router",
        goal="Determine which compliance report type is required (SAR, CTR, SANCTIONS/OFAC_REJECT, or BOTH) from transaction data or natural language.",
        backstory="""You are an expert compliance analyst with 15 years of experience in banking regulations.
        You specialize in identifying suspicious activity patterns and determining the appropriate regulatory
        reporting requirements under the Bank Secrecy Act, FinCEN requirements, and OFAC sanctions regulations.

        Your expertise includes:
        - Recognizing structuring patterns (multiple transactions just under $10,000)
        - Determining when CTR filing is required (cash transactions >= $10,000)
        - Identifying OFAC sanctions rejection cases (rejected/blocked payments due to sanctions hits)
        - Distinguishing between standard threshold reporting and suspicious behavior

        You are meticulous and never make classification errors.""",
        tools=tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=2,
    )


def create_router_task(agent: Agent, transaction_data: Any) -> Task:
    return Task(
        description=_task_description(transaction_data),
        expected_output='{"report_type": "SAR", "confidence_score": 0.95, "reasoning": "..."}',
        agent=agent,
    )
