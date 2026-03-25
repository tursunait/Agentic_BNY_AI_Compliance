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
            fields.update(self._map_suspicious_activity())
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

        # Subject state (FinCEN SAR item 10)
        f["10 State"] = subject_state

        # Subject TIN type checkbox (item 14) — check EIN or SSN/ITIN based on subject type
        if subject_tin:
            has_ssn = bool(self.subject.get("ssn"))
            f["EIN"] = "/Yes" if not has_ssn else "/Off"
            f["SSNITIN"] = "/Yes" if has_ssn else "/Off"

        # Item 1 — Initial filing type
        f["2  Check  a"] = "/Yes"

        # -----------------------------------------------------------------
        # Part III — Financial Institution Where Activity Occurred
        # -----------------------------------------------------------------
        # Institution type: Depository institution (item 47)
        f["Depository institution"] = "/Yes"

        # Primary federal regulator (item 48) — best-effort from case data
        regulator = (self.inst.get("primary_federal_regulator", "") or "").strip().upper()
        REGULATOR_FIELD = {
            "FRB": "a_10", "FEDERAL RESERVE": "a_10",
            "FDIC": "a_11",
            "NCUA": "a_12",
            "OCC": "a_13",
            "OTS": "a_14",
        }
        reg_field = REGULATOR_FIELD.get(regulator)
        if reg_field:
            f[reg_field] = "/Yes"

        inst_name = (self.inst.get("name", "") or "BNY Mellon").strip()
        inst_ein = (self.inst.get("ein", "") or "13-4941247").strip()
        if not inst_city:
            inst_city = "New York"
        if not inst_state:
            inst_state = "NY"
        if not inst_zip:
            inst_zip = "10286"
        if not inst_address:
            inst_address = "240 Greenwich Street"

        f["53  Legal name of financial institution a  Unk"] = inst_name
        f["21a  Institution  TIN"] = inst_ein
        f["55  TIN a  Unk"] = inst_ein
        # Institution TIN type — EIN
        f["EIN_2"] = "/Yes"
        f["57  Address a  Unk"] = inst_address
        f["58  City a  Unk"] = inst_city
        f["59 State"] = inst_state
        f["60  ZIPPostal Code"] = inst_zip

        # Branch / office where activity occurred (items 65–70)
        f["65  Address of branch or office where activity occurred If no branch activity involved check this box a"] = inst_address
        f["67  City"] = inst_city
        f["68  State"] = inst_state
        f["69  ZIPPostal Code"] = inst_zip
        f["70  Country 2letter code"] = (self.inst.get("country", "US") or "US").strip().upper()[:2]

        # RSSD identifier (item 66) — optional but included when provided
        rssd = str(self.inst.get("rssd", "") or "").strip()
        if rssd:
            f["66  RSSD number"] = rssd

        # Internal control / file number (item 62)
        case_id = str(self.case.get("case_id", "") or "").strip()
        if case_id:
            f["62  Internal controlfile number"] = case_id
            f["91  Internal controlfile number"] = case_id

        # -----------------------------------------------------------------
        # Part IV — Filing Institution Contact Information (static defaults)
        # These fields represent the filing institution (BNY Mellon) and
        # do not change per-case. Case-level institution data is preferred;
        # BNY Mellon data is used as fallback so the form is never blank.
        # -----------------------------------------------------------------
        # Institution type for filer (item 78)
        f["Depository institution_2"] = "/Yes"

        # Item 79 — Legal name of filing institution
        f["79 Filer name"] = inst_name

        # Item 80 — TIN of filing institution
        f["80  TIN"] = inst_ein

        # Item 81 — TIN type: EIN
        f["EIN_2"] = "/Yes"

        # Items 85–88 — Filing institution address
        # NOTE: do NOT set "85  AddressRow1" — that field renders in the
        # item 89 (Country) cell position and would corrupt the country value.
        f["85  Address"] = inst_address
        f["86  City"] = inst_city
        f["87 State"] = inst_state
        f["88 Zip postal code"] = inst_zip

        # Item 96 — Contact office (static)
        contact_officer = (self.inst.get("contact_officer", "") or "").strip()
        f[" 96 Filing institution contact office"] = contact_officer or "Financial Intelligence Unit"

        # Item 97 — Contact phone
        raw_phone = self.inst.get("contact_phone", "") or ""
        normalized_phone = self._normalize_phone(raw_phone)
        if len(normalized_phone) < 10:
            normalized_phone = "2125956009"
        f["97  Filing institution contact office phone number Include Area Code"] = normalized_phone

        # ----------------------------------------------------------------
        # Item 26 — Amount involved in this report
        # The AcroForm uses shared field names across digit cells:
        #   Text2 → cells 1-2 (both same digit; leading zeros for < $100B)
        #   Text7 → cell 3  (billions)
        #   Text10 → cell 4 (hundred millions)
        #   Text8  → cell 5 (ten millions)
        #   Text9  → cells 6-12 (7 cells, all same digit)
        # The same fields also appear in items 28 and 63 (loss), so setting
        # them is approximate for non-round amounts — the narrative carries
        # the exact figure.
        # ----------------------------------------------------------------
        amount_val = self._resolve_amount_involved()
        if amount_val > 0:
            amount_int = min(int(round(amount_val)), 999_999_999_999)
            digits = str(amount_int).zfill(12)
            f["Text2"] = digits[0]   # cells 1-2 same digit (leading zero < $100B)
            f["Text7"] = digits[2]   # cell 3 — billions
            f["Text10"] = digits[3]  # cell 4 — hundred millions
            f["Text8"] = digits[4]   # cell 5 — ten millions
            # Text9 has 7 widget instances for cells 6-12 of item 26.
            # Filling via the shared field name forces ALL cells to the same
            # digit.  Store the 7 individual digits under a private key so
            # SARReportFiler can inject them per-annotation instead.
            f["_item26_text9_cells"] = digits[5:12]

        # Filing date (MM/DD/YYYY standalone fields in AcroForm)
        now = datetime.now()
        f["MM"] = now.strftime("%m")
        f["DD"] = now.strftime("%d")
        f["YYYY"] = now.strftime("%Y")

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
    """
    Map case JSON to FinCEN CTR Form 104 AcroForm field IDs.

    Field IDs verified against the live PDF template via diagnostic fill:
      Section A: F1-1 (last name), F1-2 (first name), F1-3 (middle),
                 F1-4 (DBA), F1-5 (SSN/EIN), F1-14 (address), item-4 (DOB),
                 F1-18 (city), F1-19 (state), F1-21 (zip), F1-22 (country),
                 F1-24 (occupation)
      Section B: F1-28/30/31 (name), F1-32 (addr), F1-33 (SSN),
                 f1-43 (city), f1-44 (state), f1-46 (zip), f1-47 (country),
                 item-2 (DOB)
      Part II:  text1 (cash in), text3 (cash out), item-3 (txn date),
                f1-62..f1-67 (account numbers)
      Part III: f1-68 (inst name), f1-69 (regulator code), f1-70 (addr),
                f1-71 (EIN), f1-80 (city), f1-81 (state), f1-83 (zip),
                f1-84 (routing/MICR)
      Signature: f1-93 (title), f1-97 (preparer), f1-98 (contact),
                 f1-99 (phone), f1-102 (alt preparer), f1-105 (date)
    """

    # Regulator code lookup per CTR Form 104 instructions (Item 37)
    REGULATOR_CODES = {
        "OCC": "1", "COMPTROLLER": "1",
        "FDIC": "2",
        "FRB": "3", "FEDERAL RESERVE": "3", "FRS": "3",
        "OTS": "4",
        "NCUA": "5",
        "SEC": "6",
        "IRS": "7",
        "USPS": "8",
        "CFTC": "9",
        "STATE": "10",
    }

    def __init__(self, case_data: Dict):
        self.case = normalize_case_data(case_data)
        inst = self.case.get("institution", {})
        subject = self.case.get("subject", {})
        self.inst = inst if isinstance(inst, dict) else {}
        self.subject = subject if isinstance(subject, dict) else {}
        txns = self.case.get("transactions", [])
        self.txns = [tx for tx in txns if isinstance(tx, dict)] if isinstance(txns, list) else []

    def map_all_fields(self) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        fields.update(self._map_section_a())
        fields.update(self._map_institution())
        fields.update(self._map_transactions())
        fields.update(self._map_signature())
        return {
            key: str(value)
            for key, value in fields.items()
            if key and value is not None and value != ""
        }

    # ------------------------------------------------------------------ #
    # Part I Section A — Person on whose behalf transaction is conducted  #
    # ------------------------------------------------------------------ #
    def _map_section_a(self) -> Dict[str, str]:
        f: Dict[str, str] = {}
        full_name = (self.subject.get("name", "") or "").strip()
        subject_type = (self.subject.get("type", "") or "").lower()
        treat_as_entity = subject_type != "individual" or self._looks_like_entity_name(full_name)

        if full_name and not treat_as_entity:
            parts = full_name.split()
            f["F1-1"] = parts[-1]                    # Item 2 — last name
            f["F1-2"] = parts[0]                     # Item 3 — first name
            if len(parts) >= 3:
                f["F1-3"] = parts[1][0]              # Item 4 — middle initial
        elif full_name:
            f["F1-1"] = full_name                    # Item 2 — entity name

        # Item 5 — DBA
        dba = (self.subject.get("dba", "") or "").strip()
        if dba:
            f["F1-4"] = dba

        # Item 6 — SSN or EIN
        tin = (
            self.subject.get("tin")
            or self.subject.get("ssn")
            or self.subject.get("ein")
            or ""
        )
        if tin:
            f["F1-5"] = str(tin).strip()

        # Item 7 — Address  (F1-14 is the address field, confirmed by diagnostic)
        addr = (self.subject.get("address", "") or "").strip()
        if addr:
            f["F1-14"] = addr

        # Item 8 — Date of birth  (item-4 is the DOB field, confirmed by diagnostic)
        dob = (self.subject.get("dob", "") or self.subject.get("date_of_birth", "") or "").strip()
        if dob:
            f["item-4"] = dob

        # Items 9–11 — City / State / ZIP
        city = (self.subject.get("city", "") or "").strip()
        state = (self.subject.get("state", "") or "").strip()[:2]
        zip_code = (self.subject.get("zip", "") or self.subject.get("postal_code", "") or "").strip()
        if city:
            f["F1-18"] = city
        if state:
            f["F1-19"] = state
        if zip_code:
            f["F1-21"] = zip_code

        # Item 12 — Country code (only if non-US)
        country = (self.subject.get("country", "") or "").strip().upper()[:2]
        if country and country != "US":
            f["F1-22"] = country

        # Item 13 — Occupation / type of business
        occ = (
            self.subject.get("industry_or_occupation", "")
            or self.subject.get("occupation", "")
            or ""
        ).strip()
        if occ:
            f["F1-24"] = occ

        return f

    # ------------------------------------------------------------------ #
    # Part III — Financial institution where transaction takes place      #
    # ------------------------------------------------------------------ #
    def _map_institution(self) -> Dict[str, str]:
        f: Dict[str, str] = {}

        name = (self.inst.get("name", "") or "BNY Mellon").strip()
        ein = (self.inst.get("ein", "") or "13-4941247").strip()
        addr = (self.inst.get("address", "") or "240 Greenwich Street").strip()
        city = (self.inst.get("branch_city", "") or self.inst.get("city", "") or "New York").strip()
        state = (self.inst.get("branch_state", "") or self.inst.get("state", "") or "NY").strip()[:2]
        zip_code = (self.inst.get("zip", "") or self.inst.get("postal_code", "") or "10286").strip()
        routing = (self.inst.get("routing_number", "") or self.inst.get("micr", "") or "021000018").strip()

        f["f1-68"] = name       # Item 37 — Institution name
        f["f1-70"] = addr       # Item 38 — Address
        f["f1-71"] = ein        # Item 39 — EIN/SSN
        f["f1-80"] = city       # Item 40 — City
        f["f1-81"] = state      # Item 41 — State
        f["f1-83"] = zip_code   # Item 42 — ZIP
        f["f1-84"] = routing    # Item 43 — Routing (MICR)

        # Item 37 regulator/BSA examiner code
        regulator = (self.inst.get("primary_federal_regulator", "") or "FDIC").strip().upper()
        code = self.REGULATOR_CODES.get(regulator, self.REGULATOR_CODES.get(regulator.split()[0], "2"))
        f["f1-69"] = code

        return f

    # ------------------------------------------------------------------ #
    # Part II — Amount and type of transaction(s)                         #
    # ------------------------------------------------------------------ #
    def _map_transactions(self) -> Dict[str, str]:
        f: Dict[str, str] = {}

        # Item 28 — Date of transaction  (item-3, confirmed by diagnostic)
        if self.txns:
            ts = (self.txns[0].get("timestamp", "") or self.txns[0].get("date", "") or "").strip()
            if ts:
                date_part = ts.split("T")[0] if "T" in ts else ts
                f["item-3"] = date_part

        # Items 26 / 27 — Cash in / cash out
        # text1 = total cash in (Item 26), text3 = total cash out (Item 27)
        cash_in = 0.0
        cash_out = 0.0
        for tx in self.txns:
            amt = float(tx.get("amount_usd", 0) or tx.get("amount", 0) or 0)
            tx_type = (tx.get("type", "") or tx.get("transaction_type", "") or "").lower()
            if "out" in tx_type or "withdraw" in tx_type:
                cash_out += amt
            else:
                cash_in += amt

        total_cash = calculate_total_cash_amount(self.case)
        if cash_in > 0:
            f["text1"] = f"{cash_in:,.2f}"
        elif total_cash > 0:
            f["text1"] = f"{total_cash:,.2f}"

        if cash_out > 0:
            f["text3"] = f"{cash_out:,.2f}"      # text3 = cash out (NOT text4!)

        # Item 35 — Account numbers (f1-62 through f1-67, confirmed by diagnostic)
        accounts: List[str] = []
        seen: set = set()
        for tx in self.txns:
            for key in ("origin_account", "destination_account", "account_number"):
                val = (tx.get(key, "") or "").strip()
                if val and val not in seen:
                    seen.add(val)
                    accounts.append(val)
        acct_fields = ["f1-62", "f1-63", "f1-64", "f1-66", "f1-67"]
        for idx, acct in enumerate(accounts[:5]):
            f[acct_fields[idx]] = acct

        return f

    # ------------------------------------------------------------------ #
    # Signature / preparer section                                        #
    # ------------------------------------------------------------------ #
    def _map_signature(self) -> Dict[str, str]:
        f: Dict[str, str] = {}

        contact_name = (self.inst.get("contact_officer", "") or "Compliance Officer").strip()
        phone = (self.inst.get("contact_phone", "") or "2125956009").strip()

        # Item 44 — Title of approving official (hardcoded; static per institution)
        f["f1-93"] = "Chief Compliance Officer"

        # Item 47 — Preparer's name  (f1-97, confirmed by diagnostic)
        f["f1-97"] = contact_name

        # Item 48 — Contact person  (f1-98, confirmed by diagnostic)
        f["f1-98"] = contact_name

        # Item 49 — Telephone  (f1-99, confirmed by diagnostic)
        digits = re.sub(r"\D", "", phone)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if digits:
            f["f1-99"] = digits[:10]

        # Filing date
        now = datetime.now()
        f["f1-105"] = now.strftime("%m/%d/%Y")

        return f

    @staticmethod
    def _looks_like_entity_name(name: str) -> bool:
        upper = f" {(name or '').upper()} "
        entity_tokens = (" LLC ", " INC ", " CORP ", " CORPORATION ", " LTD ", " COMPANY ", " CO. ")
        return any(token in upper for token in entity_tokens)


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
