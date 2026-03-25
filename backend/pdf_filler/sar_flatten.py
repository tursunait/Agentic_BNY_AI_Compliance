"""
Map nested SAR case JSON (e.g. examples/sar1.json) into FinCEN-style flat keys
expected by Supabase pdf_field_mapping for report_type_code SAR.
"""

from __future__ import annotations

import re
from typing import Any


def _looks_entity(name: str, subj_type: str | None) -> bool:
    n = (name or "").upper()
    t = (subj_type or "").lower()
    if "entity" in t or "business" in t:
        return True
    for suf in ("LLC", "INC", "CORP", "LTD", "LP", "LLP", "CO.", "CORPORATION", "COMPANY"):
        if suf in n.split() or n.endswith(" " + suf) or n.endswith("." + suf.lower()):
            return True
    return " CORP" in n or n.endswith(" CORP")


def _is_nested_sar_shape(data: dict[str, Any]) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("report_type") == "SAR" and isinstance(data.get("subject"), dict):
        return True
    if isinstance(data.get("subject"), dict) and (
        isinstance(data.get("SuspiciousActivityInformation"), dict)
        or isinstance(data.get("institution"), dict)
    ):
        return True
    return False


def flatten_sar_case(data: dict[str, Any]) -> dict[str, Any]:
    """
    Build flat FinCEN key → string value dict from nested aggregator/case JSON.
    Only populates keys we can derive reliably; sparse PDF fill uses the rest.
    """
    subj = data.get("subject") or {}
    inst = data.get("institution") or {}
    alert = data.get("alert") or {}
    sai = data.get("SuspiciousActivityInformation") or {}
    dq = data.get("data_quality") or {}

    out: dict[str, str] = {}

    name = (subj.get("name") or "").strip()
    if _looks_entity(name, subj.get("type")):
        out["3"] = name
        out["4"] = ""
        out["5"] = ""
    else:
        parts = name.split()
        if len(parts) >= 2:
            out["3"] = parts[-1]
            out["4"] = " ".join(parts[:-1])
        else:
            out["3"] = name
            out["4"] = ""

    out["6"] = ", ".join(subj.get("beneficial_owners") or []) if subj.get("beneficial_owners") else ""
    out["7"] = str(subj.get("industry_or_occupation") or "")
    out["12"] = str(subj.get("country") or "")

    out["8"] = str(subj.get("address") or subj.get("street") or "")
    out["9"] = str(subj.get("city") or inst.get("branch_city") or "")
    out["10"] = str(subj.get("state") or inst.get("branch_state") or "")
    out["11"] = str(subj.get("postal_code") or subj.get("zip") or "")

    tin = subj.get("tin") or subj.get("ssn") or subj.get("ein")
    if tin:
        out["13"] = str(tin)

    out["53"] = str(inst.get("name") or "")
    out["58"] = str(inst.get("branch_city") or "")
    out["59"] = str(inst.get("branch_state") or "")
    out["57"] = str(inst.get("address") or "")
    out["60"] = str(inst.get("postal_code") or inst.get("zip") or "")
    out["61"] = str(inst.get("country") or "US")
    out["55"] = str(inst.get("tin") or inst.get("ein") or "")

    out["79"] = str(inst.get("name") or "")
    out["85"] = ", ".join(
        filter(
            None,
            [
                inst.get("name"),
                inst.get("address"),
                inst.get("branch_city"),
                inst.get("branch_state"),
                inst.get("postal_code"),
            ],
        )
    )
    out["86"] = str(inst.get("address") or "")

    out["62"] = str(alert.get("alert_id") or data.get("case_id") or "")
    out["96"] = str(inst.get("contact_officer") or "")
    out["97"] = str(inst.get("contact_phone") or "")
    out["97a"] = ""

    amt_block = sai.get("26_AmountInvolved") or {}
    if isinstance(amt_block, dict) and amt_block.get("amount_usd") is not None and not amt_block.get("no_amount"):
        out["26"] = str(amt_block["amount_usd"])

    dr = sai.get("27_DateOrDateRange") or {}
    if isinstance(dr, dict):
        if dr.get("from"):
            out["27a"] = str(dr["from"])
        if dr.get("to"):
            out["27b"] = str(dr["to"])

    if sai.get("29_Structuring"):
        out["29a"] = "Yes"
    if sai.get("33_MoneyLaundering"):
        out["33a"] = "Yes"
    if sai.get("35_OtherSuspiciousActivities"):
        out["35a"] = "Yes"

    prods = sai.get("39_ProductTypesInvolved") or []
    insts = sai.get("40_InstrumentTypesInvolved") or []
    if prods:
        out["42"] = "; ".join(str(x) for x in prods)
    if insts:
        out["41"] = "; ".join(str(x) for x in insts)

    if dq.get("notes"):
        out["19"] = str(dq["notes"])[:500]

    nar = (data.get("narrative") or "").strip()
    if nar:
        out["narrative_text"] = nar

    triggers = alert.get("trigger_reasons") or []
    if triggers:
        out["90"] = "; ".join(str(x) for x in triggers)[:300]

    return out


def maybe_flatten_sar_transaction_json(data: dict[str, Any]) -> dict[str, Any]:
    """If data looks like nested SAR case JSON, return flat FinCEN-keyed dict (with overrides)."""
    if not isinstance(data, dict) or not _is_nested_sar_shape(data):
        return data

    flat = flatten_sar_case(data)
    # Explicit FinCEN keys at top level override flattened values
    key_re = re.compile(r"^(\d+[a-z]?|narrative_text)$", re.IGNORECASE)
    for k, v in data.items():
        ks = str(k)
        if not key_re.match(ks):
            continue
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        flat[ks] = str(v).strip() if not isinstance(v, (dict, list)) else str(v)

    return flat
