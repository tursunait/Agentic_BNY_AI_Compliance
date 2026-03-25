"""
Orchestrate the full router flow: classify report type -> check KB -> validate input -> return result.
Use this from the Streamlit app or API; missing_fields can be shown to prompt the user.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Union

import requests

from backend.config.settings import settings
from backend.tools.field_mapper import determine_report_types, normalize_case_data

from loguru import logger

from router_agent.agent import classify_report_type
from router_agent.kb_client import (
    get_required_field_paths,
    get_required_fields_with_prompts,
    report_type_exists,
)
from router_agent.schema_validator import (
    get_missing_required_fields,
    normalize_input_to_single_case,
)


@dataclass
class RouterResult:
    """Result of the router agent run."""

    report_type: str
    kb_status: str  # "EXISTS" | "MISSING"
    validated_input: Dict[str, Any]
    missing_fields: List[str] = field(default_factory=list)
    missing_field_prompts: List[Dict[str, Any]] = field(default_factory=list)  # input_key, ask_user_prompt, field_label
    message: str = ""
    confidence_score: float = 0.0
    reasoning: str = ""
    # For pipeline compatibility
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



def _first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _safe_set_nested(payload: Dict[str, Any], path: str, value: Any) -> None:
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


def _derive_total_amount(case: Dict[str, Any]) -> float:
    """Derive suspicious amount from existing totals or transaction sums."""
    sai = case.get("SuspiciousActivityInformation") if isinstance(case.get("SuspiciousActivityInformation"), dict) else {}
    amount_block = sai.get("26_AmountInvolved") if isinstance(sai.get("26_AmountInvolved"), dict) else {}

    part2 = case.get("part_2_suspicious_activity") if isinstance(case.get("part_2_suspicious_activity"), dict) else {}
    findings = (
        case.get("investigation_details", {}).get("findings", {})
        if isinstance(case.get("investigation_details"), dict)
        else {}
    )
    beneficiary = findings.get("beneficiary_analysis") if isinstance(findings.get("beneficiary_analysis"), dict) else {}

    for candidate in (
        amount_block.get("amount_usd"),
        case.get("total_amount_involved"),
        case.get("amount"),
        sai.get("28_CumulativeAmount"),
        part2.get("amount_involved"),
        beneficiary.get("financial_benefit"),
    ):
        try:
            value = float(str(candidate).replace(",", "").replace("$", "").strip())
            if value > 0:
                return value
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


def _derive_suspicious_activity_dates(case: Dict[str, Any]) -> tuple[str, str]:
    sai = case.get("SuspiciousActivityInformation") if isinstance(case.get("SuspiciousActivityInformation"), dict) else {}
    date_block = sai.get("27_DateOrDateRange") if isinstance(sai.get("27_DateOrDateRange"), dict) else {}

    part2 = case.get("part_2_suspicious_activity") if isinstance(case.get("part_2_suspicious_activity"), dict) else {}
    period = part2.get("activity_period") if isinstance(part2.get("activity_period"), dict) else {}

    start = _to_mmddyyyy(_first_non_empty(date_block.get("from"), date_block.get("start"), period.get("from_date"), period.get("from")))
    end = _to_mmddyyyy(_first_non_empty(date_block.get("to"), date_block.get("end"), period.get("to_date"), period.get("to")))

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
        for value in tx_dates:
            try:
                parsed.append((datetime.strptime(value, "%m/%d/%Y"), value))
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


def _map_alternate_case_shapes(case: Dict[str, Any]) -> None:
    """Map teammate/extended payload sections into canonical keys used downstream."""
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
        full_name = _first_non_empty(case.get("subject", {}).get("name") if isinstance(case.get("subject"), dict) else None, f"{first} {last}".strip())

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

        _safe_set_nested(case, "institution.name", details.get("legal_name"))
        _safe_set_nested(case, "institution.ein", details.get("tin"))
        _safe_set_nested(case, "institution.tin", details.get("tin"))
        _safe_set_nested(case, "institution.primary_federal_regulator", regulator)
        _safe_set_nested(case, "institution.type", inst_type)
        _safe_set_nested(case, "institution.address", branch.get("branch_address"))
        _safe_set_nested(case, "institution.branch_city", branch.get("branch_city"))
        _safe_set_nested(case, "institution.branch_state", branch.get("branch_state"))
        _safe_set_nested(case, "institution.zip", branch.get("branch_zip"))
        _safe_set_nested(case, "institution.country", branch.get("branch_country"))

        _safe_set_nested(case, "financial_institution.name", details.get("legal_name"))
        _safe_set_nested(case, "financial_institution.branch_address", branch.get("branch_address"))
        _safe_set_nested(case, "financial_institution.address", branch.get("branch_address"))
        _safe_set_nested(case, "financial_institution.city", branch.get("branch_city"))
        _safe_set_nested(case, "financial_institution.state", branch.get("branch_state"))
        _safe_set_nested(case, "financial_institution.zip", branch.get("branch_zip"))
        _safe_set_nested(case, "financial_institution.country", branch.get("branch_country"))
        _safe_set_nested(case, "financial_institution.tin", details.get("tin"))
        _safe_set_nested(case, "financial_institution.ein_or_ssn", details.get("tin"))
        _safe_set_nested(case, "financial_institution.federal_regulator", regulator)
        _safe_set_nested(case, "financial_institution.primary_federal_regulator", regulator)
        _safe_set_nested(case, "financial_institution.type", inst_type)

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
    """Fill filing institution values that are static in this deployment."""
    today_mmddyyyy = datetime.now().strftime("%m/%d/%Y")
    static_values = {
        "filing_institution.address": "2334 Park Ave",
        "filing_institution.street_address": "2334 Park Ave",
        "filing_institution.city": "New York",
        "filing_institution.contact_office": "Compliance",
        "filing_institution.contact_department": "Compliance",
        "filing_institution.contact_phone": "6502798978",
        "filing_institution.country": "US",
        "filing_institution.country_code": "US",
        "filing_institution.primary_federal_regulator": "BSA",
        "filing_institution.name": "BMY Bank",
        "filing_institution.tin": "889002",
        "filing_institution.tin_type": "889002",
        "filing_institution.institution_type": "Bank",
        "filing_institution.zip": "27705",
        "filing_institution.date_filed": today_mmddyyyy,
        "sar_filing_date": today_mmddyyyy,
        # fallback keys for required_fields rows that accidentally use prompt text as input_key
        "What is the date this SAR is being filed? (MM/DD/YYYY)": today_mmddyyyy,
        "What is the street address of the filing institution?": "2334 Park Ave",
        "What is the city of the filing institution?": "New York",
        "What is the contact office or department at the filing institution?": "Compliance",
        "What is the contact phone number at the filing institution? (include area code)": "6502798978",
        "What is the 2-letter country code of the filing institution? (e.g. US)": "US",
        "Who is the primary Federal regulator of the filing institution?": "BSA",
        "What is the name of the institution filing this SAR?": "BMY Bank",
        "What is the TIN type of the filing institution? (EIN / SSN_ITIN / foreign)": "889002",
        "What type of institution is filing this SAR? (depository / casino / insurance / msb / securities / other)": "Bank",
        "What is the ZIP code of the filing institution?": "27705",
    }
    for key, value in static_values.items():
        _safe_set_nested(case, key, value)

    # Also backfill standard institution objects to avoid duplicate asks.
    _safe_set_nested(case, "institution.address", "2334 Park Ave")
    _safe_set_nested(case, "institution.branch_city", "New York")
    _safe_set_nested(case, "institution.city", "New York")
    _safe_set_nested(case, "institution.contact_officer", "Compliance")
    _safe_set_nested(case, "institution.contact_phone", "6502798978")
    _safe_set_nested(case, "institution.country", "US")
    _safe_set_nested(case, "institution.primary_federal_regulator", "BSA")
    _safe_set_nested(case, "institution.name", "BMY Bank")
    _safe_set_nested(case, "institution.ein", "889002")
    _safe_set_nested(case, "institution.tin", "889002")
    _safe_set_nested(case, "institution.type", "Bank")
    _safe_set_nested(case, "institution.zip", "27705")

    _safe_set_nested(case, "financial_institution.address", "2334 Park Ave")
    _safe_set_nested(case, "financial_institution.city", "New York")
    _safe_set_nested(case, "financial_institution.state", "NY")
    _safe_set_nested(case, "financial_institution.zip", "27705")
    _safe_set_nested(case, "financial_institution.name", "BMY Bank")
    _safe_set_nested(case, "financial_institution.ein_or_ssn", "889002")
    _safe_set_nested(case, "financial_institution.primary_federal_regulator", "BSA")


def _autofill_known_prompt_keys(case: Dict[str, Any], required_paths: List[str]) -> None:
    """Autofill values when required paths are stored as human prompt text."""
    total_amount = _derive_total_amount(case)
    today_mmddyyyy = datetime.now().strftime("%m/%d/%Y")
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
            _safe_set_nested(case, path, today_mmddyyyy)
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
                _safe_set_nested(case, path, "BMY Bank")
            elif "tin" in low:
                _safe_set_nested(case, path, "889002")
            elif "institution type" in low or "type of institution" in low:
                _safe_set_nested(case, path, "Bank")
            elif "zip" in low:
                _safe_set_nested(case, path, "27705")


def _is_redundant_activity_date_range_path(path: str) -> bool:
    low = str(path or "").strip().lower()
    if not low:
        return False
    if "27_dateordaterange" in low:
        return True
    if "activity_date_range" in low:
        return True
    return ("date range" in low or "date or date range" in low) and ("suspicious activity" in low or "activity period" in low)


def _is_redundant_total_amount_path(path: str) -> bool:
    low = str(path or "").strip().lower()
    if not low:
        return False
    if "total_amount_involved" in low:
        return True
    if "26_amountinvolved" in low or "28_cumulativeamount" in low:
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
    """
    Remove missing prompts that can be deterministically derived from the case payload.
    """
    total_amount = _derive_total_amount(case)
    today_mmddyyyy = datetime.now().strftime("%m/%d/%Y")
    date_range_text = _derive_suspicious_activity_date_range_text(case)

    filtered: List[str] = []
    for path in missing_fields:
        if _is_auto_derived_sar_filing_date_path(path):
            _safe_set_nested(case, path, today_mmddyyyy)
            continue
        if date_range_text and _is_redundant_activity_date_range_path(path):
            _safe_set_nested(case, path, date_range_text)
            continue
        if total_amount > 0 and _is_redundant_total_amount_path(path):
            _safe_set_nested(case, path, f"{total_amount:.2f}")
            continue
        filtered.append(path)
    return filtered


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
    for part in str(path or '').split('.'):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _collect_non_empty_paths(value: Any, prefix: str = '', out: Dict[str, str] | None = None) -> Dict[str, str]:
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
    low = str(missing_path or '').strip().lower()
    if not low:
        return False

    if _is_redundant_activity_date_range_path(missing_path):
        return bool(_derive_suspicious_activity_date_range_text(case))

    if _is_redundant_total_amount_path(missing_path):
        return _derive_total_amount(case) > 0

    if (
        'primary federal regulator of the filing institution' in low
        or ('filing institution' in low and 'regulator' in low)
        or low in {'filing_institution.federal_regulator', 'filing_institution.primary_federal_regulator'}
    ):
        for candidate in (
            'filing_institution.primary_federal_regulator',
            'filing_institution.federal_regulator',
            'part_4_filing_institution.primary_federal_regulator',
            'institution.primary_federal_regulator',
            'financial_institution.primary_federal_regulator',
            'financial_institution.federal_regulator',
        ):
            if _value_present(_get_nested_value(case, candidate)):
                return True
        return False

    if 'filing institution' in low and 'city' in low:
        return _value_present(_get_nested_value(case, 'filing_institution.city'))

    if 'filing institution' in low and ('address' in low or 'street' in low):
        return _value_present(_get_nested_value(case, 'filing_institution.address'))

    if 'filing institution' in low and 'phone' in low:
        return _value_present(_get_nested_value(case, 'filing_institution.contact_phone'))

    if 'filing institution' in low and 'name' in low:
        return _value_present(_get_nested_value(case, 'filing_institution.name'))

    if 'filing institution' in low and 'zip' in low:
        return _value_present(_get_nested_value(case, 'filing_institution.zip'))

    if 'filing institution' in low and 'country' in low:
        return _value_present(_get_nested_value(case, 'filing_institution.country'))

    if low in {'activity_date_range', 'activity_date_range.start', 'activity_date_range.end'}:
        start = _get_nested_value(case, 'activity_date_range.start')
        end = _get_nested_value(case, 'activity_date_range.end')
        return _value_present(start) and _value_present(end)

    return False


def _llm_semantic_resolve(case: Dict[str, Any], missing_fields: List[str], report_type: str) -> set[str]:
    api_key = str(getattr(settings, 'OPENAI_API_KEY', '') or '').strip()
    if not api_key or not missing_fields:
        return set()

    candidate_map = _collect_non_empty_paths(case)
    if not candidate_map:
        return set()

    base_url = str(getattr(settings, 'OPENAI_BASE_URL', '') or 'https://api.openai.com/v1').rstrip('/')
    model = str(getattr(settings, 'OPENAI_MODEL', '') or 'gpt-4o-mini').strip()
    url = f"{base_url}/chat/completions"

    payload = {
        'model': model,
        'temperature': 0,
        'messages': [
            {
                'role': 'system',
                'content': (
                    'You map required schema labels to equivalent populated fields. '
                    'Return only JSON with key resolved_labels (array of missing labels that are already present by equivalent meaning).'
                ),
            },
            {
                'role': 'user',
                'content': json.dumps({
                    'report_type': report_type,
                    'missing_fields': missing_fields[:8],
                    'candidate_fields': candidate_map,
                }, ensure_ascii=False),
            },
        ],
        'response_format': {'type': 'json_object'},
    }

    try:
        response = requests.post(
            url,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=6,
        )
        response.raise_for_status()
        body = response.json()
        content = (((body.get('choices') or [{}])[0].get('message') or {}).get('content') or '').strip()
        parsed = json.loads(content) if content else {}
        resolved = parsed.get('resolved_labels') if isinstance(parsed, dict) else []
        if not isinstance(resolved, list):
            return set()
        return {str(item) for item in resolved if str(item).strip()}
    except Exception as exc:
        logger.debug('LLM semantic resolver skipped due to error: {}', exc)
        return set()


def _semantic_missing_field_filter(
    case: Dict[str, Any],
    missing_fields: List[str],
    report_type: str,
) -> List[str]:
    if not missing_fields:
        return []

    unresolved: List[str] = []
    for field in missing_fields:
        if _known_semantic_match(case, field, report_type):
            continue
        unresolved.append(field)

    if not unresolved:
        return []

    resolved_by_llm = _llm_semantic_resolve(case, unresolved, report_type)
    if not resolved_by_llm:
        return unresolved

    return [field for field in unresolved if field not in resolved_by_llm]


def _extract_structured_fields_from_narrative(
    narrative_text: str,
    report_type: str,
    schema_lookup_types: List[str],
) -> Dict[str, Any]:
    """
    Use an LLM to extract structured field values from free-text narrative.

    Field definitions come from the Supabase required_fields table (input_key,
    field_label, ask_user_prompt). This keeps the router report-agnostic and
    scalable: new report types only need rows in required_fields; no code changes.
    """
    field_meta: Dict[str, Dict[str, Any]] = {}
    for rt in schema_lookup_types:
        for meta in get_required_fields_with_prompts(rt):
            key = str(meta.get("input_key") or "").strip()
            if key and key not in field_meta:
                field_meta[key] = meta

    if not field_meta:
        return {}

    fields_list = [
        {
            "input_key": key,
            "field_label": meta.get("field_label") or key,
            "ask_user_prompt": (meta.get("ask_user_prompt") or "")[:200],
        }
        for key, meta in field_meta.items()
    ]

    api_key = str(getattr(settings, "OPENAI_API_KEY", "") or "").strip()
    if not api_key:
        logger.debug("OPENAI_API_KEY not set; skipping LLM extraction from narrative")
        return {}

    base_url = str(getattr(settings, "OPENAI_BASE_URL", "") or "https://api.openai.com/v1").rstrip("/")
    model = str(getattr(settings, "OPENAI_MODEL", "") or "gpt-4o-mini").strip()
    url = f"{base_url}/chat/completions"

    system = (
        "You extract structured data from a compliance officer's narrative. "
        "Return a JSON object whose keys are exactly the input_key values from the field list. "
        "For each key, set the value to what the narrative says (string or number), or null if not mentioned. "
        "Use the field_label and ask_user_prompt to interpret what to look for. "
        "Preserve dot-notation keys as-is (e.g. subject.first_name). "
        "For dates use MM/DD/YYYY when possible. For amounts use numbers without currency symbols."
    )
    user_content = json.dumps(
        {
            "report_type": report_type,
            "narrative": narrative_text[:12000],
            "required_fields": fields_list,
        },
        ensure_ascii=False,
    )

    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
    }

    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        content = (((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        parsed = json.loads(content) if content else {}
        if not isinstance(parsed, dict):
            return {}
        out: Dict[str, Any] = {}
        for key, value in parsed.items():
            if key not in field_meta:
                continue
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            out[key] = value
        return out
    except Exception as exc:
        logger.warning("LLM extraction from narrative failed: {}", exc)
        return {}


def _merge_extracted_into_case(case_data: Dict[str, Any], extracted: Dict[str, Any]) -> None:
    """Merge LLM-extracted key-value pairs into case_data using dot-path keys."""
    for input_key, value in extracted.items():
        if not isinstance(input_key, str) or not input_key.strip():
            continue
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        _safe_set_nested(case_data, input_key.strip(), value)


def _derive_case_fields(case_data: Dict[str, Any], report_type: str) -> Dict[str, Any]:
    """Fill obvious derived fields so router does not ask for redundant inputs."""
    case = normalize_case_data(case_data)
    if not isinstance(case, dict):
        return {}

    _map_alternate_case_shapes(case)

    subject = case.get("subject") if isinstance(case.get("subject"), dict) else {}
    institution = case.get("institution") if isinstance(case.get("institution"), dict) else {}
    first_tx = _first_tx(case)

    location = str(first_tx.get("location") or "")
    fallback_city = ""
    fallback_state = ""
    if "," in location:
        city, state = location.split(",", 1)
        fallback_city = city.strip()
        fallback_state = state.strip()[:2]

    # Shared defaults
    _safe_set_nested(case, "filing_type", _first_non_empty(case.get("filing_type"), "initial"))
    _apply_static_filing_institution_defaults(case)

    # Map alternate SAR payload shape (part_2_suspicious_activity) into canonical fields.
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

    part2_amount = part2.get("amount_involved")
    if part2_amount not in (None, ""):
        _safe_set_nested(case, "SuspiciousActivityInformation.26_AmountInvolved.amount_usd", part2_amount)
        _safe_set_nested(case, "total_amount_involved", part2_amount)

    date_range_text = _derive_suspicious_activity_date_range_text(case)
    if date_range_text:
        _safe_set_nested(case, "What is the date range of the suspicious activity?", date_range_text)
        _safe_set_nested(case, "What is the date range of the suspicious activity", date_range_text)

    # Map institution -> financial_institution expected by some schemas
    _safe_set_nested(case, "financial_institution.name", institution.get("name"))
    _safe_set_nested(case, "financial_institution.city", _first_non_empty(institution.get("branch_city"), fallback_city))
    _safe_set_nested(case, "financial_institution.state", _first_non_empty(institution.get("branch_state"), fallback_state))
    _safe_set_nested(
        case,
        "financial_institution.zip",
        _first_non_empty(institution.get("zip"), institution.get("postal_code"), "00000"),
    )
    _safe_set_nested(
        case,
        "financial_institution.address",
        _first_non_empty(
            institution.get("address"),
            f"{_first_non_empty(institution.get('branch_city'), fallback_city)}, {_first_non_empty(institution.get('branch_state'), fallback_state)}".strip(", "),
        ),
    )
    _safe_set_nested(
        case,
        "financial_institution.ein_or_ssn",
        _first_non_empty(institution.get("ein"), institution.get("tin"), institution.get("ein_or_ssn"), "UNKNOWN"),
    )

    # Subject-derived fallbacks
    _safe_set_nested(case, "subject.city", _first_non_empty(subject.get("city"), fallback_city))
    _safe_set_nested(case, "subject.state", _first_non_empty(subject.get("state"), fallback_state))
    _safe_set_nested(case, "subject.zip", _first_non_empty(subject.get("zip"), subject.get("postal_code"), "00000"))
    _safe_set_nested(case, "subject.address", _first_non_empty(subject.get("address"), f"{_first_non_empty(subject.get('city'), fallback_city)}, {_first_non_empty(subject.get('state'), fallback_state)}".strip(", ")))
    _safe_set_nested(case, "subject.tin", _first_non_empty(subject.get("tin"), subject.get("ssn_or_ein"), subject.get("ssn"), subject.get("ein"), "UNKNOWN"))
    _safe_set_nested(case, "subject.date_of_birth", _first_non_empty(subject.get("date_of_birth"), subject.get("dob"), subject.get("onboarding_date"), "1900-01-01"))

    # Generic transaction date fallback used by some required fields
    tx_date = ""
    ts = str(first_tx.get("timestamp") or "")
    if ts:
        tx_date = ts.split(" ", 1)[0]
    elif str(first_tx.get("date") or "").strip():
        tx_date = str(first_tx.get("date") or "").strip()
    else:
        dr = case.get("SuspiciousActivityInformation", {}).get("27_DateOrDateRange", {}) if isinstance(case.get("SuspiciousActivityInformation"), dict) else {}
        if isinstance(dr, dict):
            tx_date = str(dr.get("from") or "")
    _safe_set_nested(case, "transaction.date", tx_date)

    # Derive suspicious amount from transaction totals to avoid asking user redundantly.
    total_amount = _derive_total_amount(case)
    if total_amount > 0:
        _safe_set_nested(case, "total_amount_involved", total_amount)
        _safe_set_nested(case, "SuspiciousActivityInformation.26_AmountInvolved.amount_usd", total_amount)
        _safe_set_nested(case, "SuspiciousActivityInformation.26_AmountInvolved.no_amount", False)
        _safe_set_nested(case, "SuspiciousActivityInformation.28_CumulativeAmount", f"{total_amount:.2f}")
        _safe_set_nested(case, "What is the total dollar amount involved in this suspicious activity?", f"{total_amount:.2f}")
        _safe_set_nested(case, "What is the total dollar amount involved in this suspicious activity", f"{total_amount:.2f}")
        _safe_set_nested(case, "total dollar amount involved in this suspicious activity", f"{total_amount:.2f}")

    rt = str(report_type or "").upper()
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
        _safe_set_nested(case, "person_a.address", _first_non_empty(subject.get("address"), f"{_first_non_empty(subject.get('city'), fallback_city)}, {_first_non_empty(subject.get('state'), fallback_state)}".strip(", ")))
        _safe_set_nested(case, "person_a.ssn_or_ein", _first_non_empty(subject.get("tin"), subject.get("ssn_or_ein"), "UNKNOWN"))

    return case


def _strip_prompt_like_top_level_keys(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Drop top-level keys that are likely prompt text artifacts from required_fields rows.
    Keeps nested canonical structures intact for downstream agents.
    """
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


def _fallback_classification_from_data(user_input: Any) -> Dict[str, Any]:
    case = normalize_case_data(user_input)
    report_types = determine_report_types(case)
    if not report_types:
        has_part2 = isinstance(case.get("part_2_suspicious_activity"), dict)
        has_red_flags = isinstance(case.get("red_flags"), list) and len(case.get("red_flags") or []) > 0
        has_narrative = isinstance(case.get("narrative"), dict) and bool((case.get("narrative") or {}).get("text"))
        if has_part2 or has_red_flags or has_narrative:
            report_types = ["SAR"]
        else:
            return {"report_type": "OTHER", "confidence_score": 0.0, "reasoning": "No clear filing signal from structured data."}
    if len(report_types) == 2:
        rt = "BOTH"
    else:
        rt = report_types[0]
    return {
        "report_type": rt,
        "confidence_score": 0.95,
        "reasoning": "Derived from transaction thresholds and suspicious-activity signals.",
    }

def _normalize_report_type(rt: str) -> str:
    rt = (rt or "").strip().upper()
    if rt in ("SAR", "CTR", "SANCTIONS", "BOTH"):
        return rt
    if rt == "OTHER":
        return "SAR"  # Default to SAR for "other" suspicious activity
    return rt


def run_router(
    user_input: Union[str, Dict[str, Any]],
    *,
    skip_llm_if_json_with_report_type: bool = True,
) -> RouterResult:
    """
    Run the router agent on user input (natural language or nested JSON).

    1. LLM classifies report type (or use report_type from JSON if present and allowed).
    2. Check Supabase KB for that report type (kb_status EXISTS | MISSING).
    3. If EXISTS: get JSON schema, validate user input against required fields.
    4. If any required field is missing, return them in missing_fields so the UI can prompt the user.

    Returns RouterResult with report_type, kb_status, validated_input, missing_fields, message.
    """
    # --- 1. Get report type ---
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
                classification = {
                    "report_type": report_type,
                    "confidence_score": 1.0,
                    "reasoning": "Taken from input report_type.",
                }
            else:
                classification = _fallback_classification_from_data(user_input)
        else:
            classification = _fallback_classification_from_data(user_input)
    else:
        try:
            classification = classify_report_type(user_input)
        except Exception as e:
            logger.warning("Router LLM classification failed, using deterministic fallback: %s", e)
            classification = _fallback_classification_from_data(user_input)

    report_type = _normalize_report_type(classification.get("report_type", "SAR"))
    confidence_score = float(classification.get("confidence_score", 0.0))
    reasoning = str(classification.get("reasoning", ""))

    # For pipeline: report_types list (BOTH => ["CTR","SAR"] etc.)
    if report_type == "BOTH":
        report_types = ["CTR", "SAR"]
    elif report_type in ("SAR", "CTR", "SANCTIONS"):
        report_types = [report_type]
    else:
        report_types = [report_type] if report_type else ["SAR"]

    # --- 2. Check KB ---
    schema_lookup_types = ["SAR", "CTR"] if report_type == "BOTH" else [report_type]
    missing_report_types = [rt for rt in schema_lookup_types if not report_type_exists(rt)]
    if missing_report_types:
        missing_label = ", ".join(missing_report_types)
        return RouterResult(
            report_type=report_type,
            report_types=report_types,
            kb_status="MISSING",
            validated_input=normalize_input_to_single_case(user_input) if isinstance(user_input, (dict, list)) else {},
            missing_fields=[],
            message=(
                f"Report type(s) '{missing_label}' are not present in the Knowledge Base. "
                "Please add them to Supabase report_types or choose another report type."
            ),
            confidence_score=confidence_score,
            reasoning=reasoning,
        )

    # --- 3. Get required fields (from Supabase required_fields table or schema) ---
    try:
        required_paths_set = set()
        for lookup_type in schema_lookup_types:
            required_paths_set.update(get_required_field_paths(lookup_type))
        required_paths = sorted(path for path in required_paths_set if str(path).strip())
    except Exception as e:
        logger.error("Failed to get required fields for {}: {}", report_type, e)
        return RouterResult(
            report_type=report_type,
            report_types=report_types,
            kb_status="EXISTS",
            validated_input=normalize_input_to_single_case(user_input) if isinstance(user_input, (dict, list)) else {},
            missing_fields=[],
            message=f"Could not load required fields for '{report_type}': {e}.",
            confidence_score=confidence_score,
            reasoning=reasoning,
        )

    # Build prompts for missing fields (from required_fields.ask_user_prompt when Supabase is used)
    def _prompts_for_missing(missing: List[str], report_tys: List[str]) -> List[Dict[str, Any]]:
        by_key: Dict[str, Dict[str, Any]] = {}
        for report_ty in report_tys:
            for meta in get_required_fields_with_prompts(report_ty):
                input_key = str(meta.get("input_key") or "").strip()
                if input_key and input_key not in by_key:
                    by_key[input_key] = meta
        return [
            by_key.get(path, {"input_key": path, "ask_user_prompt": f"Please provide value for {path}", "field_label": path})
            for path in missing
        ]

    # --- 4. Validate input ---
    if isinstance(user_input, dict):
        case_data = normalize_input_to_single_case(user_input)
    elif isinstance(user_input, list):
        case_data = normalize_input_to_single_case(user_input)
    else:
        # Natural language: extract structured fields via LLM using Supabase required_fields
        # (input_key, field_label, ask_user_prompt). Scalable for any report type.
        narrative = str(user_input).strip()
        case_data = normalize_case_data({"case_description": narrative})
        extracted = _extract_structured_fields_from_narrative(
            narrative, report_type, schema_lookup_types
        )
        _merge_extracted_into_case(case_data, extracted)

    # Reduce redundant questions by auto-filling fields derivable from existing payload.
    case_data = _derive_case_fields(case_data, report_type)
    _autofill_known_prompt_keys(case_data, required_paths)
    missing_fields = get_missing_required_fields(case_data, required_paths)
    missing_fields = _drop_auto_derived_missing_fields(case_data, missing_fields)
    missing_fields = _semantic_missing_field_filter(case_data, missing_fields, report_type)

    if missing_fields:
        message = (
            f"Report type '{report_type}' is supported. The following required fields are missing or empty: {', '.join(missing_fields)}. "
            "Please provide these details before submitting to the pipeline."
        )
        missing_field_prompts = _prompts_for_missing(missing_fields, schema_lookup_types)
    else:
        message = f"Report type '{report_type}' confirmed. All required fields are present. Ready for the rest of the pipeline."
        missing_field_prompts = []

    validated_output = case_data
    if not missing_fields:
        validated_output = normalize_case_data(
            _strip_prompt_like_top_level_keys(case_data)
        )

    return RouterResult(
        report_type=report_type,
        report_types=report_types,
        kb_status="EXISTS",
        validated_input=validated_output,
        missing_fields=missing_fields,
        missing_field_prompts=missing_field_prompts,
        message=message,
        confidence_score=confidence_score,
        reasoning=reasoning,
    )
