"""
Narrative quality validation for generated narratives (SAR, OFAC_REJECT, etc.).

Checks that the narrative passes structural, tone, and factual-alignment criteria
as defined in docs/NARRATIVE_VALIDATION.md. Report-type-specific checks are
dispatched by report_type_code.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from narrative_agent.report_types import get_report_type_from_input

# Minimum and maximum word count for a typical SAR narrative
MIN_WORDS = 50
MAX_WORDS = 1200

# Forbidden phrases that indicate non-compliant tone (legal conclusions, accusatory language)
FORBIDDEN_PHRASES = [
    r"\bguilty\b",
    r"\bcommitted\b",
    r"\bdefinitely\b",
    r"\bcertainly\s+(committed|engaged|laundered)\b",
    r"\bconvicted\b",
    r"\bproven\s+(to\s+be|that)\b",
    r"\bwithout\s+a\s+doubt\b",
    r"\bclearly\s+(committed|guilty)\b",
]

# OFAC_REJECT: avoid conflating rejection with blocking (asset freeze)
OFAC_REJECT_FORBIDDEN_PHRASES = [
    r"\bblocked\s+(the\s+)?transaction\b",
    r"\btransaction\s+was\s+blocked\b",
    r"\bblocking\s+(of\s+)?(the\s+)?(transaction|transfer)\b",
]


@dataclass
class ValidationCheck:
    """Single validation check result."""
    name: str
    passed: bool
    message: str = ""


@dataclass
class NarrativeValidationResult:
    """Result of validating a generated narrative against input and criteria."""
    passed: bool
    checks: list[ValidationCheck] = field(default_factory=list)

    def add(self, name: str, passed: bool, message: str = "") -> None:
        self.checks.append(ValidationCheck(name=name, passed=passed, message=message))
        if not passed:
            self.passed = False

    def failed_checks(self) -> list[ValidationCheck]:
        return [c for c in self.checks if not c.passed]


def _extract_text_for_grounding(data: dict[str, Any]) -> set[str]:
    """Extract strings from input that should appear or be reflected in the narrative (case-insensitive)."""
    out: set[str] = set()
    if not data:
        return out

    def collect(obj: Any) -> None:
        if isinstance(obj, str):
            s = obj.strip()
            if s and len(s) > 1:
                out.add(s.lower())
        elif isinstance(obj, dict):
            for v in obj.values():
                collect(v)
        elif isinstance(obj, list):
            for v in obj:
                collect(v)
        elif isinstance(obj, (int, float)):
            out.add(str(obj))

    collect(data)
    return out


def _run_common_checks(
    result: NarrativeValidationResult,
    text: str,
    lower: str,
    *,
    min_words: int,
    max_words: int,
) -> int:
    """Run structure and shared tone checks. Returns word_count."""
    if not text:
        result.add("narrative_non_empty", False, "Narrative is empty")
        return 0
    result.add("narrative_non_empty", True)
    has_json = bool(re.search(r'^\s*\{\s*"narrative"\s*:', text))
    result.add("no_json_in_body", not has_json, "Narrative should not contain raw JSON wrapper" if has_json else "")
    word_count = len(text.split())
    result.add(
        "word_count",
        min_words <= word_count <= max_words,
        f"Word count {word_count} outside range [{min_words}, {max_words}]" if (word_count < min_words or word_count > max_words) else "",
    )
    found = [p for p in FORBIDDEN_PHRASES if re.search(p, lower, re.IGNORECASE)]
    result.add("no_forbidden_phrases", len(found) == 0, f"Forbidden phrases found: {found}" if found else "")
    return word_count


def _validate_sar_content(
    result: NarrativeValidationResult,
    text: str,
    lower: str,
    input_data: dict[str, Any],
    word_count: int,
    *,
    strict_grounding: bool,
) -> None:
    """SAR-specific content checks."""
    if not input_data:
        result.add("subject_mentioned", True, "No input to check")
        result.add("date_mentioned", True, "No input to check")
        result.add("suspicious_patterns_reflected", True, "No input to check")
        return
    subject_name = None
    if isinstance(input_data.get("subject"), dict):
        subject_name = input_data["subject"].get("name") or input_data["subject"].get("subject_id")
    if subject_name and isinstance(subject_name, str):
        mention = subject_name.lower() in lower or ("subject" in lower and word_count > 30)
        result.add("subject_mentioned", mention, "Narrative should identify or refer to the subject")
    else:
        result.add("subject_mentioned", True, "No subject name in input")
    sai = input_data.get("SuspiciousActivityInformation") or {}
    date_range = sai.get("27_DateOrDateRange")
    if isinstance(date_range, dict) and (date_range.get("from") or date_range.get("to")):
        date_str = (date_range.get("from") or "") + " " + (date_range.get("to") or "")
        digits = re.sub(r"\D", "", date_str)
        year_ok = any(y in text for y in ("2024", "2023", "2025", "2026"))
        result.add("date_mentioned", year_ok or not digits, "Narrative should include date or date range of activity")
    else:
        result.add("date_mentioned", True, "No date in input")
    alert = input_data.get("alert") or {}
    red_flags = list(alert.get("red_flags") or [])
    for key in ("29_Structuring", "33_MoneyLaundering", "31_Fraud", "35_OtherSuspiciousActivities"):
        vals = sai.get(key)
        if isinstance(vals, list) and vals:
            red_flags.extend(str(v) for v in vals)
    if red_flags:
        has_concept = any(
            w in lower for w in ("structur", "money launder", "fraud", "suspicious", "unusual", "pattern", "transfer", "deposit", "wire", "ctr")
        )
        result.add("suspicious_patterns_reflected", has_concept, "Narrative should reflect suspicious activity types from input")
    else:
        result.add("suspicious_patterns_reflected", True, "No red flags in input")
    if strict_grounding:
        amount_usd = None
        if isinstance(sai.get("26_AmountInvolved"), dict):
            amount_usd = sai["26_AmountInvolved"].get("amount_usd")
        if amount_usd is not None:
            result.add("amount_grounded", str(int(amount_usd)) in text, f"Total amount {amount_usd} from input should appear in narrative")
        else:
            result.add("amount_grounded", True, "No amount in input")


def _validate_ofac_reject_content(
    result: NarrativeValidationResult,
    text: str,
    lower: str,
    input_data: dict[str, Any],
) -> None:
    """OFAC_REJECT-specific: rejection stated, not blocked; documents; sanctions nexus."""
    blocked_phrases = [p for p in OFAC_REJECT_FORBIDDEN_PHRASES if re.search(p, lower, re.IGNORECASE)]
    result.add(
        "rejection_not_blocking",
        len(blocked_phrases) == 0,
        "Do not say the transaction was blocked; use 'rejected' / 'not processed'" if blocked_phrases else "",
    )
    rejected_ok = "reject" in lower or "not processed" in lower or "was not processed" in lower
    result.add("rejection_stated", rejected_ok, "Narrative should clearly state the transaction was rejected / not processed")
    if not input_data:
        result.add("sanctions_nexus_mentioned", True, "No input to check")
        result.add("documents_reviewed_mentioned", True, "No input to check")
        return
    case_facts = input_data.get("case_facts") or {}
    nexus = (case_facts.get("sanctions_nexus") or "").lower()
    if nexus:
        has_nexus = any(w in lower for w in ("sanction", "iran", "itsr", "nexus", "program", "regulation", "bank melli", "beneficiary"))
        result.add("sanctions_nexus_mentioned", has_nexus, "Narrative should describe the sanctions nexus")
    else:
        result.add("sanctions_nexus_mentioned", True, "No sanctions_nexus in input")
    docs = case_facts.get("documents_reviewed")
    if isinstance(docs, list) and docs:
        has_docs = "review" in lower or "document" in lower or "invoice" in lower or "screen" in lower or "payment message" in lower
        result.add("documents_reviewed_mentioned", has_docs, "Narrative should mention documents or materials reviewed")
    else:
        result.add("documents_reviewed_mentioned", True, "No documents_reviewed in input")


def validate_narrative(
    narrative: str,
    input_data: dict[str, Any],
    *,
    report_type_code: str | None = None,
    min_words: int = MIN_WORDS,
    max_words: int = MAX_WORDS,
    strict_grounding: bool = False,
) -> NarrativeValidationResult:
    """
    Validate a generated narrative against structure, tone, and input alignment.

    Args:
        narrative: The generated narrative text.
        input_data: The original input used to generate the narrative.
        report_type_code: Report type (SAR, OFAC_REJECT). If None, read from input_data.
        min_words: Minimum acceptable word count.
        max_words: Maximum acceptable word count.
        strict_grounding: If True (SAR), require key amounts from input in narrative.

    Returns:
        NarrativeValidationResult with passed=False if any check fails.
    """
    if report_type_code is None:
        report_type_code = get_report_type_from_input(input_data)
    result = NarrativeValidationResult(passed=True)
    text = (narrative or "").strip()
    lower = text.lower()

    word_count = _run_common_checks(result, text, lower, min_words=min_words, max_words=max_words)

    if report_type_code == "OFAC_REJECT":
        _validate_ofac_reject_content(result, text, lower, input_data)
    else:
        _validate_sar_content(result, text, lower, input_data, word_count, strict_grounding=strict_grounding)

    return result
