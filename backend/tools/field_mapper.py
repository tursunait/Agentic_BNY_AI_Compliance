"""Field mapping utilities for SAR and CTR PDF filing."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List

TRUE_STRINGS = {"true", "yes", "y", "1", "t"}
FALSE_STRINGS = {"false", "no", "n", "0", "f"}
NULL_STRINGS = {"null", "none", "nil", "na", "n/a"}


def _normalize_scalar(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    cleaned = value.strip()
    lowered = cleaned.lower()
    if lowered in NULL_STRINGS:
        return None
    if lowered in TRUE_STRINGS:
        return True
    if lowered in FALSE_STRINGS:
        return False
    return cleaned


def _normalize_obj(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_obj(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_obj(item) for item in value]
    return _normalize_scalar(value)


def normalize_case_data(case_data: Any) -> Dict[str, Any]:
    """
    Normalize supported case payloads into a single dict.

    Accepts either:
    - a case dict
    - a list containing one or more case dicts (uses first dict item)
    """
    normalized = _normalize_obj(case_data)
    if isinstance(normalized, dict):
        return normalized
    if isinstance(normalized, list):
        for item in normalized:
            if isinstance(item, dict):
                return item
    return {}


class SARFieldMapper:
    """Map case JSON data to SAR PDF AcroForm field IDs."""

    REGULATOR_MAP = {
        "FRB": "/0",
        "Federal Reserve": "/0",
        "FEDERAL RESERVE": "/0",
        "FDIC": "/1",
        "NCUA": "/2",
        "OCC": "/3",
        "OTS": "/4",
    }

    LE_AGENCY_MAP = {
        "DEA": "item40a",
        "FBI": "item40b",
        "IRS": "item40c",
        "Postal Inspection": "item40d",
        "Secret Service": "item40e",
        "U.S. Customs": "item40f",
        "Other Federal": "item40g",
        "State": "item40h",
        "Local": "item40i",
    }

    def __init__(self, case_data: Dict):
        self.case = normalize_case_data(case_data)
        inst = self.case.get("institution", {})
        subject = self.case.get("subject", {})
        activity = self.case.get("SuspiciousActivityInformation", {})
        signals = self.case.get("external_signals", {})
        self.inst = inst if isinstance(inst, dict) else {}
        self.subject = subject if isinstance(subject, dict) else {}
        self.activity = activity if isinstance(activity, dict) else {}
        txns = self.case.get("transactions", [])
        self.txns = [tx for tx in txns if isinstance(tx, dict)] if isinstance(txns, list) else []
        self.signals = signals if isinstance(signals, dict) else {}
        le = self.signals.get("law_enforcement_contacted", {})
        self.le = le if isinstance(le, dict) else {}

    def map_all_fields(self) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        fields.update(self._map_institution())
        fields.update(self._map_suspect())
        fields.update(self._map_suspicious_activity())
        fields.update(self._map_law_enforcement())
        fields.update(self._map_contact())
        fields.update(self._map_narrative())
        return {
            key: str(value)
            for key, value in fields.items()
            if value is not None and value != ""
        }

    def _map_institution(self) -> Dict[str, str]:
        f: Dict[str, str] = {}

        f["item2"] = self.inst.get("name", "")
        f["item6"] = self.inst.get("branch_city", "")

        state = (self.inst.get("branch_state", "") or "")[:2]
        city = self.inst.get("branch_city", "") or ""
        f["item7"] = state
        f["item9"] = f"{city}, {state}".strip(", ")
        f["item10"] = city
        f["item11-1"] = state

        regulator = (self.inst.get("primary_federal_regulator", "") or "").strip()
        if regulator in self.REGULATOR_MAP:
            f["item5"] = self.REGULATOR_MAP[regulator]
        elif regulator.upper() in self.REGULATOR_MAP:
            f["item5"] = self.REGULATOR_MAP[regulator.upper()]

        accounts = self._collect_accounts()
        acct_fields = ["item14a", "item14b", "item14c", "item14d"]
        acct_open_fields = ["item14a-1", "item14b-1", "item14c-1", "item14d-1"]
        for idx, acct in enumerate(accounts[:4]):
            f[acct_fields[idx]] = acct
            f[acct_open_fields[idx]] = "/Yes"

        return f

    def _collect_accounts(self) -> List[str]:
        seen = set()
        out: List[str] = []
        for tx in self.txns:
            for key in ("origin_account", "destination_account"):
                acct = tx.get(key, "")
                if acct and acct not in seen:
                    seen.add(acct)
                    out.append(acct)
        return out

    def _map_suspect(self) -> Dict[str, str]:
        f: Dict[str, str] = {}

        full_name = self.subject.get("name", "") or ""
        subject_type = (self.subject.get("type", "") or "").lower()

        if subject_type == "individual":
            parsed = self._split_individual_name(full_name)
            f["item15"] = parsed["last"]
            f["item16"] = parsed["first"]
            f["item17"] = parsed["middle"]
        else:
            f["item15"] = full_name

        f["item23"] = self.subject.get("country", "US")
        f["item26"] = self.subject.get("industry_or_occupation", "")

        # Conservative defaults for missing explicit data.
        f["item28"] = "/1"
        f["item30"] = "/6"
        f["item31"] = "/1"
        return f

    @staticmethod
    def _split_individual_name(name: str) -> Dict[str, str]:
        clean = " ".join((name or "").split())
        if not clean:
            return {"first": "", "middle": "", "last": ""}
        parts = clean.split(" ")
        if len(parts) == 1:
            return {"first": parts[0], "middle": "", "last": parts[0]}
        if len(parts) == 2:
            return {"first": parts[0], "middle": "", "last": parts[1]}
        return {"first": parts[0], "middle": " ".join(parts[1:-1]), "last": parts[-1]}

    def _map_suspicious_activity(self) -> Dict[str, str]:
        f: Dict[str, str] = {}

        date_range = self.activity.get("27_DateOrDateRange", {})
        self._apply_mmddyyyy(date_range.get("from", ""), ("item33-1", "item33-2", "item33-3"), f)
        self._apply_mmddyyyy(date_range.get("to", ""), ("item33-4", "item33-5", "item33-6"), f)

        amount_info = self.activity.get("26_AmountInvolved", {})
        amount_val = amount_info.get("amount_usd", 0.0) or 0.0
        amount_digits = f"{int(float(amount_val)):011d}"
        for idx, field_id in enumerate(
            [
                "item34-1",
                "item34-2",
                "item34-3",
                "item34-4",
                "item34-5",
                "item34-6",
                "item34-7",
                "item34-8",
                "item34-9",
                "item34-10",
                "item34-11",
            ]
        ):
            f[field_id] = amount_digits[idx]

        if self._has_items(self.activity.get("29_Structuring")):
            f["item35a"] = "/Yes"
        if self._has_items(self.activity.get("30_TerroristFinancing")):
            f["item35t"] = "/Yes"
        if self._contains_case_insensitive(self.activity.get("31_Fraud"), "wire"):
            f["item35r"] = "/Yes"
        if self._has_items(self.activity.get("33_MoneyLaundering")):
            f["item35a"] = "/Yes"
        if self._has_items(self.activity.get("38_MortgageFraud")):
            f["item35p"] = "/Yes"

        other = self.activity.get("35_OtherSuspiciousActivities", [])
        if self._has_items(other):
            f["item35s"] = "/Yes"
            f["item35s-1"] = "; ".join(self._to_list(other))[:100]

        f["item38"] = "/1"
        f["item39"] = "/1"
        return f

    @staticmethod
    def _has_items(value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return len(SARFieldMapper._to_list(value)) > 0

    @staticmethod
    def _to_list(value) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            out: List[str] = []
            for item in value:
                normalized = _normalize_obj(item)
                if normalized in (None, False, ""):
                    continue
                if isinstance(normalized, list):
                    out.extend(SARFieldMapper._to_list(normalized))
                else:
                    out.append(str(normalized))
            return out
        normalized = _normalize_obj(value)
        if normalized in (None, False, ""):
            return []
        return [str(normalized)]

    @staticmethod
    def _contains_case_insensitive(value, needle: str) -> bool:
        for item in SARFieldMapper._to_list(value):
            if needle.lower() in item.lower():
                return True
        return False

    @staticmethod
    def _apply_mmddyyyy(value: str, field_ids, out: Dict[str, str]) -> None:
        text = (value or "").strip()
        if not text:
            return
        parts = text.split("/")
        if len(parts) != 3:
            return
        out[field_ids[0]] = parts[0]
        out[field_ids[1]] = parts[1]
        out[field_ids[2]] = parts[2]

    @staticmethod
    def _as_bool(value: Any) -> bool:
        normalized = _normalize_scalar(value)
        if isinstance(normalized, bool):
            return normalized
        if isinstance(normalized, (int, float)):
            return normalized != 0
        return bool(normalized)

    def _map_law_enforcement(self) -> Dict[str, str]:
        f: Dict[str, str] = {}
        if not self._as_bool(self.le.get("contacted", False)):
            return f

        agency = self.le.get("agency") or ""
        contact_name = self.le.get("contact_name") or ""
        phone = self.le.get("phone", "") or ""

        matched = self.LE_AGENCY_MAP.get(agency)
        if matched:
            f[matched] = "/Yes"
        elif agency:
            f["item40j"] = "/Yes"
            f["item40j-1"] = agency[:80]

        if contact_name:
            f["item41"] = contact_name

        digits = re.sub(r"\D", "", phone)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) >= 10:
            f["item42-1"] = digits[:3]
            f["item42-2"] = digits[3:10]

        return f

    def _map_contact(self) -> Dict[str, str]:
        f: Dict[str, str] = {}
        contact_name = self.inst.get("contact_officer", "") or ""
        if contact_name:
            parts = contact_name.split(" ", 1)
            f["item46"] = parts[0]
            f["item45"] = parts[1] if len(parts) > 1 else parts[0]
        f["item48"] = "Compliance Officer"

        phone = self.inst.get("contact_phone", "") or ""
        digits = re.sub(r"\D", "", phone)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) >= 10:
            f["item49-1"] = digits[:3]
            f["item49-2"] = digits[3:10]

        now = datetime.now()
        f["item50-1"] = now.strftime("%m")
        f["item50-2"] = now.strftime("%d")
        f["item50-3"] = now.strftime("%Y")
        return f

    def _map_narrative(self) -> Dict[str, str]:
        provided_narrative = _normalize_scalar(self.case.get("narrative"))
        if isinstance(provided_narrative, str) and provided_narrative:
            return {"item51": provided_narrative[:4000]}

        blocks: List[str] = []
        subject_name = self.subject.get("name", "Unknown")
        occ = self.subject.get("industry_or_occupation", "")
        country = self.subject.get("country", "")

        line = f"SUBJECT: {subject_name}"
        if occ:
            line += f", {occ}"
        if country:
            line += f" ({country})"
        blocks.append(line)

        prior = self.subject.get("prior_sars", [])
        if prior:
            blocks.append(f"PRIOR SARS: {', '.join(prior)}")

        date_range = self.activity.get("27_DateOrDateRange", {})
        if date_range.get("from") and date_range.get("to"):
            blocks.append(
                f"ACTIVITY PERIOD: {date_range['from']} through {date_range['to']}"
            )

        alert = self.case.get("alert", {})
        flags = self._to_list(alert.get("red_flags")) + self._to_list(
            alert.get("trigger_reasons")
        )
        if flags:
            blocks.append(f"RED FLAGS DETECTED: {'; '.join(flags)}")

        amount = self.activity.get("26_AmountInvolved", {}).get("amount_usd", 0)
        if amount:
            blocks.append(f"TOTAL AMOUNT INVOLVED: ${float(amount):,.2f}")

        if self.txns:
            blocks.append(f"TRANSACTIONS ({len(self.txns)} total):")
            for tx in self.txns:
                ts = tx.get("timestamp", "")
                usd = float(tx.get("amount_usd", 0) or 0)
                orig = tx.get("origin_account", "")
                dest = tx.get("destination_account", "")
                loc = tx.get("location", "")
                note = tx.get("notes", "")
                tx_line = f"  {ts}: ${usd:,.2f} from {orig} to {dest}"
                if loc:
                    tx_line += f" | {loc}"
                if note:
                    tx_line += f" | {note}"
                blocks.append(tx_line)

        if self._has_items(self.activity.get("29_Structuring")):
            blocks.append(
                "STRUCTURING: " + "; ".join(self._to_list(self.activity["29_Structuring"]))
            )
        if self._has_items(self.activity.get("33_MoneyLaundering")):
            blocks.append(
                "MONEY LAUNDERING INDICATORS: "
                + "; ".join(self._to_list(self.activity["33_MoneyLaundering"]))
            )
        if self._has_items(self.activity.get("31_Fraud")):
            blocks.append("FRAUD: " + "; ".join(self._to_list(self.activity["31_Fraud"])))
        if self._has_items(self.activity.get("35_OtherSuspiciousActivities")):
            blocks.append(
                "OTHER SUSPICIOUS ACTIVITY: "
                + "; ".join(self._to_list(self.activity["35_OtherSuspiciousActivities"]))
            )

        dq_notes = self.case.get("data_quality", {}).get("notes", "")
        if dq_notes:
            blocks.append(f"NOTES: {dq_notes}")

        media = self._to_list(self.signals.get("adverse_media"))
        if media:
            blocks.append("ADVERSE MEDIA: " + "; ".join(media))

        narrative = "\n\n".join(blocks)
        return {"item51": narrative[:4000]}


class CTRFieldMapper:
    """Map case JSON data to CTR PDF AcroForm field IDs."""

    # The CTR template in this repo has generic field IDs. These defaults are
    # intentionally simple and can be overridden by providing case_data["ctr_field_map"].
    DEFAULT_FIELD_MAP = {
        "institution_name": "item-1",
        "institution_ein": "item-2",
        "institution_address": "item-3",
        "institution_city_state": "item-4",
        "conductor_name": "item-5",
        "conductor_country": "item-6",
        "total_cash_amount": "item-7",
        "prepared_date": "item-8",
        "contact_name": "text1",
        "contact_phone": "text2",
        "account_numbers": "text3",
        "transaction_summary": "text4",
    }

    def __init__(self, case_data: Dict):
        self.case = normalize_case_data(case_data)
        inst = self.case.get("institution", {})
        subject = self.case.get("subject", {})
        activity = self.case.get("SuspiciousActivityInformation", {})
        self.inst = inst if isinstance(inst, dict) else {}
        self.subject = subject if isinstance(subject, dict) else {}
        self.activity = activity if isinstance(activity, dict) else {}
        txns = self.case.get("transactions", [])
        self.txns = [tx for tx in txns if isinstance(tx, dict)] if isinstance(txns, list) else []

        overrides = self.case.get("ctr_field_map", {})
        if isinstance(overrides, dict):
            self.field_map = {**self.DEFAULT_FIELD_MAP, **overrides}
        else:
            self.field_map = dict(self.DEFAULT_FIELD_MAP)

    def map_all_fields(self) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        fields.update(self._map_institution())
        fields.update(self._map_conductor())
        fields.update(self._map_transactions())
        fields.update(self._map_contact())
        return {
            key: str(value)
            for key, value in fields.items()
            if key and value is not None and value != ""
        }

    def _put(self, out: Dict[str, str], logical_key: str, value: str) -> None:
        field_id = self.field_map.get(logical_key)
        if field_id and value not in ("", None):
            out[field_id] = str(value)

    def _map_institution(self) -> Dict[str, str]:
        f: Dict[str, str] = {}
        name = self.inst.get("name", "")
        ein = self.inst.get("ein", "")
        addr = self.inst.get("address", "")
        city = self.inst.get("branch_city", "")
        state = (self.inst.get("branch_state", "") or "")[:2]
        city_state = f"{city}, {state}".strip(", ")

        self._put(f, "institution_name", name)
        self._put(f, "institution_ein", ein)
        self._put(f, "institution_address", addr)
        self._put(f, "institution_city_state", city_state)
        return f

    def _map_conductor(self) -> Dict[str, str]:
        f: Dict[str, str] = {}
        full_name = self.subject.get("name", "") or "Unknown"
        country = self.subject.get("country", "US")
        self._put(f, "conductor_name", full_name)
        self._put(f, "conductor_country", country)
        return f

    def _map_transactions(self) -> Dict[str, str]:
        f: Dict[str, str] = {}
        total_cash = calculate_total_cash_amount(self.case)
        if total_cash:
            self._put(f, "total_cash_amount", f"{total_cash:,.2f}")

        accounts = []
        seen = set()
        for tx in self.txns:
            for key in ("origin_account", "destination_account"):
                value = tx.get(key, "")
                if value and value not in seen:
                    seen.add(value)
                    accounts.append(value)
        if accounts:
            self._put(f, "account_numbers", ", ".join(accounts[:4]))

        tx_lines: List[str] = []
        for tx in self.txns[:6]:
            ts = tx.get("timestamp", "")
            amt = float(tx.get("amount_usd", 0) or 0)
            instrument = tx.get("instrument_type", "")
            product = tx.get("product_type", "")
            tx_lines.append(f"{ts} ${amt:,.2f} {instrument} {product}".strip())
        if tx_lines:
            self._put(f, "transaction_summary", " | ".join(tx_lines)[:500])

        return f

    def _map_contact(self) -> Dict[str, str]:
        f: Dict[str, str] = {}
        contact_name = self.inst.get("contact_officer", "")
        if contact_name:
            self._put(f, "contact_name", contact_name)

        phone = self.inst.get("contact_phone", "") or ""
        digits = re.sub(r"\D", "", phone)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) >= 10:
            self._put(f, "contact_phone", f"{digits[:3]}-{digits[3:10]}")

        now = datetime.now().strftime("%m/%d/%Y")
        self._put(f, "prepared_date", now)
        return f


def _to_list(value) -> List[str]:
    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            out.extend(_to_list(item))
        return out
    normalized = _normalize_obj(value)
    if normalized in (None, False, ""):
        return []
    return [str(normalized)]


def _is_cash_transaction(tx: Dict) -> bool:
    instrument = (tx.get("instrument_type", "") or "").strip().lower()
    product = (tx.get("product_type", "") or "").strip().lower()
    tx_type = (tx.get("type", "") or "").strip().lower()
    text = " ".join([instrument, product, tx_type])
    return any(token in text for token in ("cash", "currency", "cash_deposit"))


def calculate_total_cash_amount(case_data: Any) -> float:
    """Return total cash amount from case transactions."""
    normalized_case = normalize_case_data(case_data)
    total = 0.0
    for tx in normalized_case.get("transactions", []):
        if not isinstance(tx, dict):
            continue
        if _is_cash_transaction(tx):
            total += float(tx.get("amount_usd", 0) or 0)
    return total


def has_suspicious_activity(case_data: Any) -> bool:
    """Detect SAR-triggering indicators from structured case fields."""
    normalized_case = normalize_case_data(case_data)
    activity = normalized_case.get("SuspiciousActivityInformation", {})
    suspicious_fields = (
        "29_Structuring",
        "30_TerroristFinancing",
        "31_Fraud",
        "33_MoneyLaundering",
        "35_OtherSuspiciousActivities",
        "38_MortgageFraud",
    )
    if any(_to_list(activity.get(name)) for name in suspicious_fields):
        return True

    alert = normalized_case.get("alert", {})
    if any(_text_indicates_suspicion(x) for x in _to_list(alert.get("red_flags"))):
        return True
    if any(_text_indicates_suspicion(x) for x in _to_list(alert.get("trigger_reasons"))):
        return True
    return False


def _text_indicates_suspicion(text: str) -> bool:
    lowered = str(_normalize_scalar(text) or "").lower()
    if not lowered:
        return False
    suspicious_tokens = (
        "structur",
        "fraud",
        "launder",
        "suspicious",
        "terror",
        "sanction",
        "unusual",
        "no apparent",
        "kiting",
        "embezz",
    )
    if any(token in lowered for token in suspicious_tokens):
        return True
    # Threshold-only phrases should not force SAR by themselves.
    non_sar_tokens = (
        "exceeds $10,000",
        "exceeds 10,000",
        "10k",
        "threshold",
    )
    if any(token in lowered for token in non_sar_tokens):
        return False
    return False


def determine_report_types(case_data: Any) -> List[str]:
    """
    Determine which report(s) to file.

    Rules:
    - total cash >= 10,000 => CTR
    - suspicious activity indicators => SAR
    - both can be required
    """
    report_types: List[str] = []
    if calculate_total_cash_amount(case_data) >= 10000.0:
        report_types.append("CTR")
    if has_suspicious_activity(case_data):
        report_types.append("SAR")
    return report_types
