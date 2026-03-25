"""Input validation helpers for case submission."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any


def validate_transaction_data(case_data: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []

    subject = case_data.get("subject") if isinstance(case_data.get("subject"), dict) else {}
    if not subject.get("name"):
        errors.append("Subject name is required")

    suspicious = case_data.get("SuspiciousActivityInformation")
    if not isinstance(suspicious, dict):
        errors.append("SuspiciousActivityInformation is required")
        suspicious = {}

    amount_data = suspicious.get("26_AmountInvolved")
    if not isinstance(amount_data, dict):
        errors.append("Amount involved is required")
        amount = 0.0
    else:
        amount = float(amount_data.get("amount_usd") or 0.0)
    if amount < 0:
        errors.append("Amount cannot be negative")

    date_range = suspicious.get("27_DateOrDateRange") if isinstance(suspicious.get("27_DateOrDateRange"), dict) else {}
    if not date_range.get("from") or not date_range.get("to"):
        errors.append("Activity date range is required")

    txns = case_data.get("transactions")
    if not isinstance(txns, list) or len(txns) == 0:
        errors.append("At least one transaction is required")

    return len(errors) == 0, errors


def build_manual_case_payload(
    subject_name: str,
    subject_type: str,
    country: str,
    occupation: str,
    amount_usd: float,
    from_date: date,
    to_date: date,
    instrument_type: str,
    suspicious_flags: list[str],
    notes: str,
) -> dict[str, Any]:
    case_id = f"CASE-MANUAL-{from_date.strftime('%Y%m%d')}-{subject_name[:8].upper().replace(' ', '')}"
    return {
        "case_id": case_id,
        "report_type": "Initial",
        "subject": {
            "subject_id": f"SUB-{subject_name[:6].upper().replace(' ', '')}",
            "name": subject_name,
            "type": subject_type,
            "country": country or "US",
            "industry_or_occupation": occupation,
        },
        "institution": {
            "name": "Example Community Bank",
            "branch_city": "New York",
            "branch_state": "NY",
            "primary_federal_regulator": "FDIC",
        },
        "alert": {
            "alert_id": "MANUAL-ENTRY",
            "trigger_reasons": suspicious_flags,
            "risk_score": 0.5 if suspicious_flags else 0.1,
            "red_flags": suspicious_flags,
        },
        "SuspiciousActivityInformation": {
            "26_AmountInvolved": {"amount_usd": float(amount_usd), "no_amount": False},
            "27_DateOrDateRange": {
                "from": from_date.strftime("%m/%d/%Y"),
                "to": to_date.strftime("%m/%d/%Y"),
            },
            "29_Structuring": ["Potential structuring pattern"] if "Structuring" in suspicious_flags else [],
            "30_TerroristFinancing": ["Potential TF"] if "Terrorist Financing" in suspicious_flags else [],
            "31_Fraud": [flag for flag in suspicious_flags if "Fraud" in flag],
            "33_MoneyLaundering": ["Potential ML"] if "Money Laundering" in suspicious_flags else [],
            "35_OtherSuspiciousActivities": [
                flag for flag in suspicious_flags
                if flag not in {"Structuring", "Terrorist Financing", "Money Laundering"}
            ],
            "39_ProductTypesInvolved": ["Manual Entry"],
            "40_InstrumentTypesInvolved": [instrument_type],
        },
        "transactions": [
            {
                "tx_id": "manual-1",
                "timestamp": from_date.strftime("%m/%d/%Y 12:00:00"),
                "amount_usd": float(amount_usd),
                "origin_account": "MANUAL-ORIGIN",
                "destination_account": "MANUAL-DEST",
                "location": "Manual Entry",
                "product_type": "Manual",
                "instrument_type": instrument_type,
                "notes": notes or "Submitted through Streamlit manual form",
            }
        ],
        "narrative": notes or "Manual case submitted through Streamlit UI.",
    }


def build_text_case_payload(free_text: str, subject_name: str = "Unknown Subject") -> dict[str, Any]:
    """
    Build a minimally structured case payload from free-text analyst input.
    """
    text = (free_text or "").strip()
    parsed_case = _try_parse_structured_case(text)
    if parsed_case:
        return parsed_case

    lowered = text.lower()
    today = date.today()
    extracted_dates = _extract_dates(text)
    from_dt = min(extracted_dates) if extracted_dates else today
    to_dt = max(extracted_dates) if extracted_dates else today

    amounts = _extract_amounts(text)
    explicit_total = _extract_explicit_total_amount(text)
    total_amount = explicit_total if explicit_total is not None else (sum(amounts) if len(amounts) > 1 else (amounts[0] if amounts else 0.0))

    suspicious = []
    if any(token in lowered for token in ("structur", "below ctr", "just below")):
        suspicious.append("Structuring")
    if any(token in lowered for token in ("launder", "money laundering")):
        suspicious.append("Money Laundering")
    if any(token in lowered for token in ("fraud", "scam")):
        suspicious.append("Fraud")
    if any(token in lowered for token in ("terror", "sanction")):
        suspicious.append("Terrorist Financing")

    return {
        "case_id": f"CASE-TEXT-{today.strftime('%Y%m%d')}",
        "source_type": "free_text",
        "raw_user_input": text,
        "subject": {
            "subject_id": f"SUB-{subject_name[:8].upper().replace(' ', '')}",
            "name": subject_name or "Unknown Subject",
            "type": "Individual",
            "country": "US",
        },
        "institution": {
            "name": "Example Community Bank",
            "branch_city": "New York",
            "branch_state": "NY",
            "primary_federal_regulator": "FDIC",
        },
        "SuspiciousActivityInformation": {
            "26_AmountInvolved": {"amount_usd": total_amount, "no_amount": total_amount == 0.0},
            "27_DateOrDateRange": {
                "from": from_dt.strftime("%m/%d/%Y"),
                "to": to_dt.strftime("%m/%d/%Y"),
            },
            "29_Structuring": ["Pattern described in free-text input"] if "Structuring" in suspicious else [],
            "30_TerroristFinancing": ["Potential sanctions/terror concern"] if "Terrorist Financing" in suspicious else [],
            "31_Fraud": ["Potential fraud signal"] if "Fraud" in suspicious else [],
            "33_MoneyLaundering": ["Potential AML concern"] if "Money Laundering" in suspicious else [],
            "35_OtherSuspiciousActivities": suspicious,
            "39_ProductTypesInvolved": ["Narrative Input"],
            "40_InstrumentTypesInvolved": ["Unspecified"],
        },
        "transactions": [
            {
                "tx_id": "text-1",
                "timestamp": from_dt.strftime("%m/%d/%Y 12:00:00"),
                "amount_usd": total_amount,
                "origin_account": "UNKNOWN",
                "destination_account": "UNKNOWN",
                "location": "Unknown",
                "product_type": "Narrative Input",
                "instrument_type": "Unspecified",
                "notes": text,
            }
        ],
        "narrative": text,
    }


def _try_parse_structured_case(text: str) -> dict[str, Any] | None:
    """Accept raw JSON or fenced JSON pasted into text input."""
    if not text:
        return None

    candidates = [text.strip()]

    # Support markdown fenced JSON blocks.
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    for block in fenced:
        block = block.strip()
        if block:
            candidates.append(block)

    # Support prose + embedded JSON object.
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidates.append(text[first_brace : last_brace + 1].strip())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue

        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    return item
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_explicit_total_amount(text: str) -> float | None:
    pattern = re.compile(
        r"(?i)(?:total(?:\s+dollar\s+amount)?(?:\s+involved)?|totaling|amount\s+involved)\D{0,20}(?:USD|US\$|\$)?\s*(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
    )
    match = pattern.search(text or "")
    if not match:
        return None
    return _to_float(match.group(1))


def _extract_amounts(text: str) -> list[float]:
    found: list[float] = []
    if not text:
        return found

    patterns = [
        re.compile(r"(?i)(?:USD|US\$|\$)\s*(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"),
        re.compile(r"(?i)(?:amount|deposit|withdrawal|transfer|wire|cash|total(?:ing)?|involved)\D{0,12}(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d{4,}(?:\.\d{1,2})?)"),
    ]

    for pattern in patterns:
        for match in pattern.findall(text):
            value = _to_float(match)
            if value > 0:
                found.append(value)

    # Deduplicate while preserving order.
    seen = set()
    deduped: list[float] = []
    for value in found:
        key = round(value, 2)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def _extract_dates(text: str) -> list[date]:
    dates: list[date] = []
    if not text:
        return dates

    date_patterns = [
        (r"\b\d{1,2}/\d{1,2}/\d{4}\b", "%m/%d/%Y"),
        (r"\b\d{4}-\d{2}-\d{2}\b", "%Y-%m-%d"),
    ]
    for pattern, fmt in date_patterns:
        for token in re.findall(pattern, text):
            try:
                dates.append(datetime.strptime(token, fmt).date())
            except ValueError:
                continue
    return dates


def _to_float(value: Any) -> float:
    try:
        if isinstance(value, str):
            cleaned = value.replace(",", "").replace("$", "").strip()
            return float(cleaned or 0)
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
