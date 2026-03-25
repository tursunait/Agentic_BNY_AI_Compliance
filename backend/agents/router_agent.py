from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Union

from crewai import Agent, Task
from crewai import LLM


# ---------------------------------------------------------------------------
# Helper utilities (ported from legacy router_agent/run.py)
# ---------------------------------------------------------------------------

def _first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _safe_set_nested(payload: Dict[str, Any], path: str, value: Any) -> None:
    """Set a dot-path key in payload only when the leaf is currently empty."""
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

    start = _to_mmddyyyy(_first_non_empty(
        date_block.get("from"), date_block.get("start"),
        period.get("from_date"), period.get("from"),
    ))
    end = _to_mmddyyyy(_first_non_empty(
        date_block.get("to"), date_block.get("end"),
        period.get("to_date"), period.get("to"),
    ))

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


# ---------------------------------------------------------------------------
# Case shape normalisation
# ---------------------------------------------------------------------------

def _map_alternate_case_shapes(case: Dict[str, Any]) -> None:
    """
    Map teammate/extended payload sections (part_1_subject_information,
    part_2_suspicious_activity, part_3_institution_where_occurred,
    part_4_filing_institution, report_metadata) into the canonical keys
    used by downstream agents.
    """
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
    """Fill filing institution values that are static for this deployment."""
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
        # Canonical institution fallbacks
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


def derive_and_normalize_case(case_data: Dict[str, Any], report_type: str) -> Dict[str, Any]:
    """
    Full pre-processing pass on a case dict:
    1. Map alternate payload shapes (part_1/2/3/4) into canonical keys
    2. Apply static filing institution defaults
    3. Derive and fill activity dates, amounts, subject/institution fallbacks
    4. Derive CTR person_a fields when applicable

    Returns the enriched case dict (mutated in place and returned).
    """
    from backend.tools.field_mapper import normalize_case_data
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

    # Map part_2_suspicious_activity into canonical SAR fields
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

    # Map institution → financial_institution
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

    # Subject fallbacks
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

    # Transaction date fallback
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

    # Amount derivation
    total_amount = _derive_total_amount(case)
    if total_amount > 0:
        _safe_set_nested(case, "total_amount_involved", total_amount)
        _safe_set_nested(case, "SuspiciousActivityInformation.26_AmountInvolved.amount_usd", total_amount)
        _safe_set_nested(case, "SuspiciousActivityInformation.26_AmountInvolved.no_amount", False)
        _safe_set_nested(case, "SuspiciousActivityInformation.28_CumulativeAmount", f"{total_amount:.2f}")
        _safe_set_nested(case, "What is the total dollar amount involved in this suspicious activity?", f"{total_amount:.2f}")
        _safe_set_nested(case, "total dollar amount involved in this suspicious activity", f"{total_amount:.2f}")

    # CTR-specific person_a fields
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
    """
    Drop top-level keys that are likely prompt-text artifacts (contain '?',
    start with question words, or are long sentences without dot-notation).
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


def fallback_classify(user_input: Any) -> Dict[str, Any]:
    """
    Fallback classification when the LLM router fails or returns no report types.
    Uses deterministic rules: SAR flags, red_flags list, narrative presence.
    """
    from backend.tools.field_mapper import normalize_case_data, determine_report_types
    case = normalize_case_data(user_input)
    report_types = determine_report_types(case)
    if not report_types:
        has_part2 = isinstance(case.get("part_2_suspicious_activity"), dict)
        has_red_flags = isinstance(case.get("red_flags"), list) and len(case.get("red_flags") or []) > 0
        has_narrative = isinstance(case.get("narrative"), dict) and bool((case.get("narrative") or {}).get("text"))
        if has_part2 or has_red_flags or has_narrative:
            report_types = ["SAR"]
        else:
            return {
                "report_types": [],
                "report_type": "OTHER",
                "confidence_score": 0.0,
                "reasoning": "No clear filing signal from structured data.",
            }
    return {
        "report_types": report_types,
        "report_type": report_types[0],
        "confidence_score": 0.95,
        "reasoning": "Derived from transaction thresholds and suspicious-activity signals.",
    }


# ---------------------------------------------------------------------------
# CrewAI agent and task factories
# ---------------------------------------------------------------------------

def create_router_agent(llm: LLM, tools: list) -> Agent:
    return Agent(
        role="Report Type Classifier",
        goal="Accurately determine which report(s) are required (SAR, CTR, OFAC_REJECT, or combinations) based on transaction data",
        backstory="""You are an expert compliance analyst with 15 years of experience
        in banking regulations. You specialize in identifying suspicious activity patterns
        and determining the appropriate regulatory reporting requirements. You understand
        the Bank Secrecy Act, FinCEN requirements, and OFAC sanctions regulations intimately.

        Your expertise includes:
        - Recognizing structuring patterns (multiple transactions just under $10,000)
        - Determining when CTR filing is required (cash transactions >= $10,000)
        - Identifying OFAC sanctions rejection cases (rejected/blocked payments due to sanctions hits)
        - Distinguishing between standard threshold reporting and suspicious behavior

        You are meticulous and never make classification errors, as the wrong report
        type could lead to regulatory violations.""",
        tools=tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=3,
    )


def create_router_task(agent: Agent, transaction_data: dict) -> Task:
    return Task(
        description=f"""
        Analyze the following transaction data and classify filing requirement.

        Transaction Data:
        {json.dumps(transaction_data, indent=2)}

        Output a JSON object with:
        - report_types: ["SAR"], ["CTR"], ["CTR","SAR"], ["OFAC_REJECT"], or []
        - confidence_score: float between 0 and 1
        - total_cash_amount: float
        - reasoning: concise factual explanation
        - kb_status: "EXISTS" or "MISSING"
        - narrative_description: 2-4 factual sentences

        Classification policy:
        - OFAC_REJECT when the case involves a rejected/blocked transaction due to OFAC sanctions
          (look for: report_type_code="OFAC_REJECT", date_of_rejection, sanctions_program,
          beneficiary_fi in a sanctioned country, or case_facts.disposition containing "Rejected")
        - SAR when suspicious activity indicators exist (structuring, fraud, money laundering, etc.)
        - CTR when total cash activity >= $10,000
        - BOTH (CTR + SAR) when both conditions apply
        - [] when neither applies

        Return JSON only.
        """,
        expected_output="""{
"report_types": ["CTR", "SAR"],
"confidence_score": 0.95,
"total_cash_amount": 15500.0,
"reasoning": "Cash exceeds threshold and suspicious indicators are present",
"kb_status": "EXISTS",
"narrative_description": "Natural language description of the pattern"
}""",
        agent=agent,
    )
