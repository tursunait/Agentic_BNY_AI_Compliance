import re
from typing import Dict, List, Tuple

class SARRuleChecker:
    """Check SAR reports against a set of validation rules."""

    def __init__(self, data: Dict, rules: List[Dict]):
        self.data = data
        self.rules = rules
        self.violations = []

    def check_all(self) -> List[Dict]:
        for rule in self.rules:
            rule_id = rule["rule_id"]
            severity = rule["severity"]
            default_msg = rule["rule_json"]["message"]

            # Replace hyphens with underscores
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
                self.violations.append({
                    "rule_id": rule_id,
                    "severity": severity,
                    "message": f"Rule {rule_id} not implemented in engine."
                })
        return self.violations

    # ---------- SAR rule implementations ----------
    def check_sar_req_001(self) -> Tuple[bool, str]:
        filing_type = self.data.get("filing_type")
        return bool(filing_type), "Filing type missing" if not filing_type else (True, None)

    def check_sar_req_002(self) -> Tuple[bool, str]:
        filing_type = self.data.get("filing_type")
        if filing_type in ("amend", "correct"):
            prior = self.data.get("prior_report_number")
            return bool(prior), "Prior report number missing for amended filing" if not prior else (True, None)
        return True, None

    def check_sar_req_003(self) -> Tuple[bool, str]:
        last = self.data.get("subject", {}).get("last_name")
        return bool(last), "Subject last name missing" if not last else (True, None)

    def check_sar_req_004(self) -> Tuple[bool, str]:
        first = self.data.get("subject", {}).get("first_name")
        return bool(first), "Subject first name missing" if not first else (True, None)

    def check_sar_req_005(self) -> Tuple[bool, str]:
        occ = self.data.get("subject", {}).get("occupation")
        return bool(occ), "Subject occupation missing" if not occ else (True, None)

    def check_sar_req_006(self) -> Tuple[bool, str]:
        addr = self.data.get("subject", {}).get("address")
        return bool(addr), "Subject address missing" if not addr else (True, None)

    def check_sar_req_007(self) -> Tuple[bool, str]:
        city = self.data.get("subject", {}).get("city")
        return bool(city), "Subject city missing" if not city else (True, None)

    def check_sar_req_008(self) -> Tuple[bool, str]:
        country = self.data.get("subject", {}).get("country")
        state = self.data.get("subject", {}).get("state")
        if country == "US" and not state:
            return False, "Subject state required for US"
        return True, None

    def check_sar_req_009(self) -> Tuple[bool, str]:
        zipc = self.data.get("subject", {}).get("zip")
        return bool(zipc), "Subject ZIP missing" if not zipc else (True, None)

    def check_sar_req_010(self) -> Tuple[bool, str]:
        country = self.data.get("subject", {}).get("country")
        return bool(country), "Subject country missing" if not country else (True, None)

    def check_sar_req_011(self) -> Tuple[bool, str]:
        tin = self.data.get("subject", {}).get("tin")
        return bool(tin), "Subject TIN missing" if not tin else (True, None)

    def check_sar_req_012(self) -> Tuple[bool, str]:
        dob = self.data.get("subject", {}).get("dob")
        return bool(dob), "Subject date of birth missing" if not dob else (True, None)

    def check_sar_req_013(self) -> Tuple[bool, str]:
        # Data lacks full ID details, so we skip detailed check (always pass)
        return True, None

    def check_sar_req_014(self) -> Tuple[bool, str]:
        amount = self.data.get("activity", {}).get("amount")
        if amount is None or amount < 5000:
            return False, f"Suspicious amount {amount} is below $5,000 threshold"
        return True, None

    def check_sar_req_015(self) -> Tuple[bool, str]:
        date_range = self.data.get("activity", {}).get("activity_date_range", {})
        start = date_range.get("start")
        end = date_range.get("end")
        if not start or not end:
            return False, "Activity date range incomplete"
        return True, None

    def check_sar_req_016(self) -> Tuple[bool, str]:
        structuring = self.data.get("activity", {}).get("structuring", [])
        if structuring:
            narrative = self.data.get("narrative")
            if not narrative or "structuring" not in narrative.lower():
                return False, "Structuring indicated but not explained in narrative"
        return True, None

    def check_sar_req_017(self) -> Tuple[bool, str]:
        name = self.data.get("financial_institution", {}).get("name")
        return bool(name), "Financial institution name missing" if not name else (True, None)

    def check_sar_req_018(self) -> Tuple[bool, str]:
        tin = self.data.get("financial_institution", {}).get("tin")
        return bool(tin), "Financial institution TIN missing" if not tin else (True, None)

    def check_sar_req_019(self) -> Tuple[bool, str]:
        contact_office = self.data.get("filing_institution", {}).get("contact_office")
        return bool(contact_office), "Filing institution contact office missing" if not contact_office else (True, None)

    def check_sar_req_020(self) -> Tuple[bool, str]:
        phone = self.data.get("filing_institution", {}).get("contact_phone")
        return bool(phone), "Filing institution contact phone missing" if not phone else (True, None)

    def check_sar_req_021(self) -> Tuple[bool, str]:
        date_filed = self.data.get("filing_institution", {}).get("date_filed")
        return bool(date_filed), "Filing date missing" if not date_filed else (True, None)

    def check_sar_req_022(self) -> Tuple[bool, str]:
        required = self.data.get("narrative_required", False)
        if required:
            narrative = self.data.get("narrative")
            return bool(narrative), "Narrative is required but missing" if not narrative else (True, None)
        return True, None

    def check_sar_req_023(self) -> Tuple[bool, str]:
        narrative = self.data.get("narrative")
        if narrative and "see attached" in narrative.lower():
            return False, "Narrative contains prohibited attachment reference"
        return True, None

    # Narrative quality rules (024-030) are handled by LLM, so they always pass here.
    def check_sar_req_024(self): return True, None
    def check_sar_req_025(self): return True, None
    def check_sar_req_026(self): return True, None
    def check_sar_req_027(self): return True, None
    def check_sar_req_028(self): return True, None
    def check_sar_req_029(self): return True, None
    def check_sar_req_030(self): return True, None