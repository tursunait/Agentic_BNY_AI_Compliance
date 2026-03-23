import re
from datetime import datetime
from typing import Dict, List, Tuple

class CTRRuleChecker:
    """Check CTR reports against a set of validation rules."""

    def __init__(self, data: Dict, rules: List[Dict]):
        self.data = data
        self.rules = rules
        self.violations = []

    def check_all(self) -> List[Dict]:
        """Execute all applicable CTR rules."""
        for rule in self.rules:
            rule_id = rule["rule_id"]
            severity = rule["severity"]
            default_msg = rule["rule_json"]["message"]

            # Replace hyphens with underscores to match method names
            method_name = f"check_{rule_id.lower().replace('-', '_')}"
            check_method = getattr(self, method_name, None)
            if check_method:
                passed, custom_msg = check_method()
                if not passed:
                    self.violations.append({
                        "rule_id": rule_id,
                        "severity": severity,
                        "message": custom_msg or default_msg
                    })
            else:
                # If method not implemented, treat as violation (optional)
                self.violations.append({
                    "rule_id": rule_id,
                    "severity": severity,
                    "message": f"Rule {rule_id} not implemented in engine."
                })
        return self.violations

    # ---------- Rule implementations (unchanged from previous, but ensure all exist) ----------
    def check_ctr_req_001(self) -> Tuple[bool, str]:
        cash_in = self.data.get("transaction", {}).get("cash_in", 0)
        cash_out = self.data.get("transaction", {}).get("cash_out", 0)
        return (cash_in > 10000 or cash_out > 10000), None

    def check_ctr_req_002(self) -> Tuple[bool, str]:
        tx_date = self.data.get("transaction", {}).get("date")
        if not tx_date:
            return False, "Transaction date missing"
        try:
            datetime.strptime(tx_date, "%Y-%m-%d")
            return True, None
        except ValueError:
            return False, "Transaction date invalid format (expected YYYY-MM-DD)"

    def check_ctr_req_003(self) -> Tuple[bool, str]:
        tx_date = self.data.get("transaction", {}).get("date")
        file_date = self.data.get("signature", {}).get("date")
        if not tx_date or not file_date:
            return False, "Missing transaction or filing date"
        try:
            tx = datetime.strptime(tx_date, "%Y-%m-%d")
            fd = datetime.strptime(file_date, "%Y-%m-%d")
            return (fd - tx).days <= 15, None
        except ValueError:
            return False, "Date format error"

    def check_ctr_req_004(self) -> Tuple[bool, str]:
        last_name = self.data.get("section_a", {}).get("last_name")
        return bool(last_name), "Last name missing" if not last_name else (True, None)

    def check_ctr_req_005(self) -> Tuple[bool, str]:
        first_name = self.data.get("section_a", {}).get("first_name")
        return bool(first_name), "First name missing" if not first_name else (True, None)

    def check_ctr_req_006(self) -> Tuple[bool, str]:
        addr = self.data.get("section_a", {}).get("address")
        return bool(addr), "Street address missing" if not addr else (True, None)

    def check_ctr_req_007(self) -> Tuple[bool, str]:
        city = self.data.get("section_a", {}).get("city")
        return bool(city), "City missing" if not city else (True, None)

    def check_ctr_req_008(self) -> Tuple[bool, str]:
        country = self.data.get("section_a", {}).get("country")
        state = self.data.get("section_a", {}).get("state")
        if country in ("US", "CA", "MX") and not state:
            return False, f"State required for country {country}"
        return True, None

    def check_ctr_req_009(self) -> Tuple[bool, str]:
        zip_code = self.data.get("section_a", {}).get("zip")
        return bool(zip_code), "ZIP/Postal code missing" if not zip_code else (True, None)

    def check_ctr_req_010(self) -> Tuple[bool, str]:
        country = self.data.get("section_a", {}).get("country")
        return bool(country), "Country code missing" if not country else (True, None)

    def check_ctr_req_011(self) -> Tuple[bool, str]:
        tin = self.data.get("section_a", {}).get("ssn_or_ein")
        return bool(tin), "TIN missing" if not tin else (True, None)

    def check_ctr_req_012(self) -> Tuple[bool, str]:
        tin = self.data.get("section_a", {}).get("ssn_or_ein")
        country = self.data.get("section_a", {}).get("country")
        if not tin:
            return True, None  # already flagged by 011
        clean = re.sub(r'[-\s]', '', tin)
        if country == "US":
            if len(clean) != 9 or not clean.isdigit():
                return False, "US TIN must be exactly 9 digits"
            if clean in ("000000000", "999999999", "123456789"):
                return False, "Invalid US TIN pattern"
        else:
            if len(clean) > 25:
                return False, "Foreign TIN too long (max 25 characters)"
        return True, None

    def check_ctr_req_013(self) -> Tuple[bool, str]:
        # Data does not contain TIN type, so always fail
        return False, "TIN type missing (EIN/SSN-ITIN/Foreign)"

    def check_ctr_req_014(self) -> Tuple[bool, str]:
        dob = self.data.get("section_a", {}).get("dob")
        return bool(dob), "Date of birth missing" if not dob else (True, None)

    def check_ctr_req_015(self) -> Tuple[bool, str]:
        id_type = self.data.get("section_a", {}).get("id_type")
        id_num = self.data.get("section_a", {}).get("id_number")
        id_issuer = self.data.get("section_a", {}).get("id_issued_by")
        if not id_type or not id_num or not id_issuer:
            return False, "Identification details incomplete"
        return True, None

    def check_ctr_req_016(self) -> Tuple[bool, str]:
        name = self.data.get("institution", {}).get("name")
        return bool(name), "Financial institution name missing" if not name else (True, None)

    def check_ctr_req_017(self) -> Tuple[bool, str]:
        ein = self.data.get("institution", {}).get("ein_or_ssn")
        if not ein:
            return False, "Financial institution EIN missing"
        clean = re.sub(r'[-\s]', '', ein)
        if len(clean) != 9 or not clean.isdigit():
            return False, "Financial institution EIN must be 9 digits"
        if clean in ("000000000", "999999999", "123456789"):
            return False, "Invalid EIN pattern"
        return True, None

    def check_ctr_req_018(self) -> Tuple[bool, str]:
        addr = self.data.get("institution", {}).get("address")
        return bool(addr), "Institution address missing" if not addr else (True, None)

    def check_ctr_req_019(self) -> Tuple[bool, str]:
        city = self.data.get("institution", {}).get("city")
        return bool(city), "Institution city missing" if not city else (True, None)

    def check_ctr_req_020(self) -> Tuple[bool, str]:
        state = self.data.get("institution", {}).get("state")
        return bool(state), "Institution state missing" if not state else (True, None)

    def check_ctr_req_021(self) -> Tuple[bool, str]:
        zipc = self.data.get("institution", {}).get("zip")
        return bool(zipc), "Institution ZIP missing" if not zipc else (True, None)

    def check_ctr_req_022(self) -> Tuple[bool, str]:
        reg = self.data.get("institution", {}).get("regulator_code")
        return bool(reg), "Regulator code missing" if not reg else (True, None)

    def check_ctr_req_023(self) -> Tuple[bool, str]:
        # Data has no institution type field, so always fail
        return False, "Financial institution type missing"

    def check_ctr_req_024(self) -> Tuple[bool, str]:
        contact = self.data.get("signature", {}).get("contact_name")
        return bool(contact), "Contact office name missing" if not contact else (True, None)

    def check_ctr_req_025(self) -> Tuple[bool, str]:
        phone = self.data.get("signature", {}).get("contact_phone")
        if not phone:
            return False, "Contact phone missing"
        if not re.search(r'\d', phone):
            return False, "Contact phone contains no digits"
        return True, None

    def check_ctr_req_026(self) -> Tuple[bool, str]:
        prohibited = ["AKA", "COMPUTER GENERATED", "CUSTOMER", "N/A", "NOT APPLICABLE", "XX"]
        text_fields = []

        def collect(obj):
            if isinstance(obj, dict):
                for v in obj.values():
                    collect(v)
            elif isinstance(obj, list):
                for item in obj:
                    collect(item)
            elif isinstance(obj, str):
                text_fields.append(obj.upper())

        collect(self.data)
        for field in text_fields:
            for word in prohibited:
                if word in field:
                    return False, f"Prohibited word '{word}' detected"
        return True, None

    def check_ctr_req_027(self) -> Tuple[bool, str]:
        multiple = self.data.get("multiple_persons", False)
        if multiple:
            sec_b = self.data.get("section_b", {})
            if not any(sec_b.values()):
                return False, "Multiple persons flag set but second person info missing"
        return True, None

    def check_ctr_req_028(self) -> Tuple[bool, str]:
        foreign_in = self.data.get("transaction", {}).get("foreign_cash_in")
        foreign_out = self.data.get("transaction", {}).get("foreign_cash_out")
        country = self.data.get("transaction", {}).get("foreign_country")
        if (foreign_in or foreign_out) and not country:
            return False, "Foreign currency present but country code missing"
        return True, None

    def check_ctr_req_029(self) -> Tuple[bool, str]:
        accounts = self.data.get("transaction", {}).get("account_numbers", [])
        return len(accounts) > 0, "No account numbers provided"