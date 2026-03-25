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

    def map_all_fields(self, template_variant: str = "legacy") -> Dict[str, str]:
        fields: Dict[str, str] = {}
        variant = (template_variant or "legacy").strip().lower()
        if variant == "fincen_acroform":
            fields.update(self._map_fincen_acroform())
        elif variant == "all":
            fields.update(self._map_institution())
            fields.update(self._map_suspect())
            fields.update(self._map_suspicious_activity())
            fields.update(self._map_law_enforcement())
            fields.update(self._map_contact())
            fields.update(self._map_narrative())
            fields.update(self._map_fincen_acroform())
        else:
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
        fallback_city, fallback_state = self._city_state_from_txns()
        city = (self.inst.get("branch_city", "") or "").strip() or fallback_city
        state = ((self.inst.get("branch_state", "") or "").strip()[:2] or fallback_state)
        zip_code = (
            (self.inst.get("zip", "") or "").strip()
            or (self.inst.get("postal_code", "") or "").strip()
        )

        f["item6"] = city
        f["item7"] = state
        f["item9"] = self._compose_address_line(city, state, zip_code)
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
        treat_as_entity = subject_type != "individual" or self._looks_like_entity_name(full_name)

        if not treat_as_entity:
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

        amount_val = self._resolve_amount_involved()
        amount_digits = f"{int(round(amount_val)):011d}"
        # Some SAR templates expose item34 as one text field, while others split
        # it across 11 digit boxes (item34-1..item34-11). Populate both styles.
        f["item34"] = str(int(round(amount_val)))
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

        summary_fields, summary_notes = self._map_summary_characterization_fields()
        f.update(summary_fields)
        if summary_notes:
            f["item35s"] = "/Yes"
            f["item35s-1"] = "; ".join(summary_notes)[:100]

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

        amount = self._resolve_amount_involved()
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

    def _resolve_amount_involved(self) -> float:
        """
        Resolve SAR amount with fallbacks used by different pipeline stages.
        Priority:
        1) SuspiciousActivityInformation.26_AmountInvolved.amount_usd
        2) top-level total_amount_involved
        3) sum(transactions[].amount_usd|amount)
        """
        amount_info = self.activity.get("26_AmountInvolved", {})
        if isinstance(amount_info, dict):
            amount = self._to_float(amount_info.get("amount_usd"))
            if amount > 0:
                return amount

        amount = self._to_float(self.case.get("total_amount_involved"))
        if amount > 0:
            return amount

        tx_total = 0.0
        for tx in self.txns:
            if not isinstance(tx, dict):
                continue
            tx_total += self._to_float(tx.get("amount_usd"))
            if self._to_float(tx.get("amount_usd")) == 0:
                tx_total += self._to_float(tx.get("amount"))
        return tx_total

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            if isinstance(value, str):
                cleaned = value.replace(",", "").replace("$", "").strip()
                return float(cleaned or 0)
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def _map_summary_characterization_fields(self) -> tuple[Dict[str, str], List[str]]:
        """
        Map SAR item 35 summary characterization checkboxes conservatively.
        Returns checkbox fields plus free-text notes for item35s-1.
        """
        fields: Dict[str, str] = {}
        notes: List[str] = []

        structuring = self._to_list(self.activity.get("29_Structuring"))
        fraud = self._to_list(self.activity.get("31_Fraud"))
        money_laundering = self._to_list(self.activity.get("33_MoneyLaundering"))
        other = self._to_list(self.activity.get("35_OtherSuspiciousActivities"))
        mortgage = self._to_list(self.activity.get("38_MortgageFraud"))
        terrorist = self._to_list(self.activity.get("30_TerroristFinancing"))

        if structuring or money_laundering:
            fields["item35a"] = "/Yes"
        if mortgage:
            fields["item35t"] = "/Yes"

        fraud_text = " | ".join(fraud).lower()
        keyword_map = {
            "item35c": ("check fraud", "check"),
            "item35d": ("kiting",),
            "item35e": ("commercial loan",),
            "item35f": ("computer intrusion", "cyber", "malware", "phishing"),
            "item35g": ("consumer loan",),
            "item35h": ("counterfeit check",),
            "item35i": ("counterfeit credit", "counterfeit debit"),
            "item35j": ("counterfeit instrument",),
            "item35k": ("credit card fraud", "credit card"),
            "item35l": ("debit card fraud", "debit card"),
            "item35m": ("embezzlement", "defalcation"),
            "item35n": ("false statement",),
            "item35o": ("financial institution fraud", "institution fraud"),
            "item35p": ("identity theft", "account takeover"),
            "item35r": ("mail fraud",),
            "item35u": ("mysterious disappearance",),
        }
        for field_id, needles in keyword_map.items():
            if any(needle in fraud_text for needle in needles):
                fields[field_id] = "/Yes"

        # No dedicated legacy checkbox for wire fraud; keep it in free-text.
        if "wire fraud" in fraud_text:
            notes.append("Wire fraud")

        notes.extend(other)
        notes.extend(terrorist)

        if not fields and (structuring or fraud or money_laundering or other or mortgage or terrorist):
            fields["item35s"] = "/Yes"

        return fields, notes

    def _map_fincen_acroform(self) -> Dict[str, str]:
        """
        Map key values to the newer FinCEN SAR AcroForm field names.

        This map is intentionally conservative (text fields only) because
        checkbox/radio export values in this template are not stable labels.
        """
        f: Dict[str, str] = {}

        subject_type = (self.subject.get("type", "") or "").lower()
        subject_name = self.subject.get("name", "") or ""
        treat_as_entity = subject_type != "individual" or self._looks_like_entity_name(subject_name)
        if not treat_as_entity:
            parsed = self._split_individual_name(subject_name)
            f["3  Individuals last name or entitys legal name a Unk"] = parsed["last"]
            f["4  First name a Unk"] = parsed["first"]
            f["5  Middle initial"] = parsed["middle"][:1]
        else:
            f["3  Individuals last name or entitys legal name a Unk"] = subject_name

        fallback_city, fallback_state = self._city_state_from_txns()
        subject_city = (self.subject.get("city", "") or "").strip() or fallback_city
        subject_state = (self.subject.get("state", "") or "").strip()[:2] or fallback_state
        subject_zip = (
            (self.subject.get("zip", "") or "").strip()
            or (self.subject.get("postal_code", "") or "").strip()
        )
        subject_address = (self.subject.get("address", "") or "").strip()
        if not subject_address:
            subject_address = self._compose_address_line(subject_city, subject_state, subject_zip)

        f["7  Occupation or type of business"] = self.subject.get("industry_or_occupation", "")
        f["8 Address a Unk"] = subject_address
        f["9  City a Unk"] = subject_city
        f["11  ZIPPostal Code a Unk"] = subject_zip
        country = (self.subject.get("country", "US") or "US").strip().upper()[:2]
        f["12  Country code"] = country

        subject_tin = (
            self.subject.get("tin")
            or self.subject.get("ssn")
            or self.subject.get("ein")
            or ""
        )
        f["13  TIN a Unk"] = subject_tin

        alert_email = (
            self.case.get("institution", {}).get("contact_email")
            if isinstance(self.case.get("institution"), dict)
            else ""
        ) or ""
        f["19 Email adress (If available)"] = alert_email

        inst_city = (self.inst.get("branch_city", "") or "").strip() or fallback_city
        inst_state = (self.inst.get("branch_state", "") or "").strip()[:2] or fallback_state
        inst_zip = (
            (self.inst.get("zip", "") or "").strip()
            or (self.inst.get("postal_code", "") or "").strip()
        )
        inst_address = (self.inst.get("address", "") or "").strip()
        if not inst_address:
            inst_address = self._compose_address_line(inst_city, inst_state, inst_zip)

        f["53  Legal name of financial institution a  Unk"] = self.inst.get("name", "")
        f["55  TIN a  Unk"] = self.inst.get("ein", "")
        f["57  Address a  Unk"] = inst_address
        f["58  City a  Unk"] = inst_city
        f["59 State"] = inst_state
        f["60  ZIPPostal Code"] = inst_zip

        contact_name = self.inst.get("contact_officer", "") or ""
        f["79 Filer name"] = contact_name
        f[" 96 Filing institution contact office"] = "Compliance Office"
        f["97  Filing institution contact office phone number Include Area Code"] = (
            self._normalize_phone(self.inst.get("contact_phone", ""))
        )

        if self._as_bool(self.le.get("contacted", False)):
            f["92  LE contact agency"] = self.le.get("agency", "") or ""
            f["93  LE contact name"] = self.le.get("contact_name", "") or ""
            f["94  LE contact phone number Include Area Code"] = self._normalize_phone(
                self.le.get("phone", "")
            )

        narrative = self._map_narrative().get("item51", "")
        if narrative:
            f["Narrative"] = narrative

        return f

    def _city_state_from_txns(self) -> tuple[str, str]:
        for tx in self.txns:
            location = tx.get("location", "") or ""
            if "," in location:
                city, state = location.split(",", 1)
                return city.strip(), state.strip()[:2]
        return "", ""

    @staticmethod
    def _compose_address_line(city: str, state: str, zip_code: str) -> str:
        city = (city or "").strip()
        state = (state or "").strip()
        zip_code = (zip_code or "").strip()
        parts = []
        if city or state:
            parts.append(", ".join(part for part in [city, state] if part))
        if zip_code:
            if parts:
                parts[0] = f"{parts[0]} {zip_code}".strip()
            else:
                parts.append(zip_code)
        return parts[0] if parts else ""

    @staticmethod
    def _looks_like_entity_name(name: str) -> bool:
        upper = f" {(name or '').upper()} "
        entity_tokens = (" LLC ", " INC ", " CORP ", " CORPORATION ", " LTD ", " COMPANY ", " CO. ")
        return any(token in upper for token in entity_tokens)

    @staticmethod
    def _normalize_phone(value: str) -> str:
        digits = re.sub(r"\D", "", value or "")
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        return digits[:10] if digits else ""


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
