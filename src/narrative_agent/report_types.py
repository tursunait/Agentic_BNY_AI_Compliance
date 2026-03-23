"""
Report type registry: required input keys, agent role/goal, and optional local narrative guidance.

Used for routing, validation, and KB fallback when Supabase has no row for a report type.
Keeps report-type-specific logic in one place to avoid scattering special cases.
"""

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Registry entry per report type
# ---------------------------------------------------------------------------


@dataclass
class ReportTypeSpec:
    """Specification for one narrative report type."""

    code: str
    display_name: str
    required_input_keys: frozenset[str]
    agent_role: str
    agent_goal: str
    agent_backstory: str
    prompt_input_label: str
    expected_output_hint: str
    # Optional local narrative instructions (used when KB has no row for this type)
    local_instructions: str | None = None
    local_examples_text: str | None = None


# ---------------------------------------------------------------------------
# SAR
# ---------------------------------------------------------------------------

SAR_SPEC = ReportTypeSpec(
    code="SAR",
    display_name="Suspicious Activity Report",
    required_input_keys=frozenset({"case_id", "subject", "SuspiciousActivityInformation"}),
    agent_role="SAR Narrative Writer",
    agent_goal="Write accurate, factual SAR narratives using only the provided suspicious activity data. Never invent or assume facts.",
    agent_backstory=(
        "You are a compliance analyst who drafts SAR narrative sections. "
        "You strictly use only the information given in the input. You never add names, dates, amounts, or events that are not explicitly in the data."
    ),
    prompt_input_label="suspicious activity information",
    expected_output_hint="SAR narrative paragraph",
    local_instructions=None,
    local_examples_text=None,
)

# ---------------------------------------------------------------------------
# OFAC Rejected Transaction Report (sanctions)
# ---------------------------------------------------------------------------

OFAC_REJECT_INSTRUCTIONS = """
Sanctions Rejected Transaction Report — narrative guidance

Required elements (use only data from the input):
1. Summarize the attempted transaction: type, amount, currency, date, parties (originator, beneficiary, FIs).
2. Explain what triggered sanctions review (e.g., automated screening — beneficiary country, entity name, etc.).
3. Describe the sanctions nexus: why the transaction touched a sanctions program (e.g., beneficiary in sanctioned jurisdiction, sanctioned entity, list match).
4. Explain why the transaction was rejected (cite program/regulation if provided, e.g., Iran — ITSR, 31 C.F.R. Part 560).
5. Mention documents or materials reviewed (payment message, invoice, screening output, etc.) as stated in the input.
6. Clearly state that the transaction was NOT processed (rejected). Do not use "blocked" unless the input explicitly says blocked; prefer "rejected" for transactions that were not executed.

Things to avoid:
- Do not confuse rejection with blocking (blocking implies asset freeze; rejection means the transaction was not processed).
- Do not speculate beyond the facts in the input (no unsupported conclusions about intent or future actions).
- Do not add names, amounts, dates, or program details not present in the input.
- Use a factual, neutral tone suitable for regulatory documentation.

Output: One continuous narrative paragraph. Return JSON only: {"narrative": "..."}.
"""

OFAC_REJECT_EXAMPLE = """
--- Example: Sanctions rejected wire (Iran) ---
Example narrative text:
On March 6, 2026, First National Bank rejected an outbound wire transfer in the amount of 18,450.00 USD from ABC Import Services (4210 Industrial Blvd, Houston, TX) destined for Tehran Industrial Supply Co. (Tehran, Iran), with beneficiary financial institution Bank Melli Iran. The transaction was flagged by automated screening due to the beneficiary country (Iran). Bank Melli Iran is a sanctioned entity under the Iran Transactions and Sanctions Regulations (ITSR), 31 C.F.R. Part 560. The payment reference indicated settlement for industrial components (invoice INV-2026-0112). The institution reviewed the payment message, invoice INV-2026-0112, customer account records, and sanctions screening output. Based on the sanctions nexus, the transaction was rejected and was not processed. This report is prepared for record-keeping and regulatory compliance.

Why this example is effective:
It summarizes the transaction, states the trigger (automated screening — beneficiary country), describes the sanctions nexus (Iran, sanctioned FI), cites the program (ITSR), lists documents reviewed, and clearly states the transaction was rejected and not processed without conflating rejection with blocking.
"""

OFAC_REJECT_SPEC = ReportTypeSpec(
    code="OFAC_REJECT",
    display_name="OFAC Sanctions Rejected Transaction Report",
    required_input_keys=frozenset({"case_id", "transaction", "case_facts"}),
    agent_role="OFAC Compliance Narrative Writer",
    agent_goal="Write accurate, factual narratives for sanctions rejected transaction reports using only the provided case data. Never invent or assume facts; clearly state the transaction was rejected and not processed.",
    agent_backstory=(
        "You are a BSA/OFAC compliance officer who drafts narratives for rejected transaction reports. "
        "You use only the information given in the input. You clearly distinguish rejection (transaction not processed) from blocking (asset freeze). "
        "You do not speculate beyond the facts provided."
    ),
    prompt_input_label="case data (transaction, sanctions nexus, documents reviewed, disposition)",
    expected_output_hint="sanctions rejected transaction narrative paragraph",
    local_instructions=OFAC_REJECT_INSTRUCTIONS.strip(),
    local_examples_text=OFAC_REJECT_EXAMPLE.strip(),
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY: dict[str, ReportTypeSpec] = {
    SAR_SPEC.code: SAR_SPEC,
    OFAC_REJECT_SPEC.code: OFAC_REJECT_SPEC,
}


def get_report_type_spec(report_type_code: str) -> ReportTypeSpec:
    """Return the spec for a report type. Raises KeyError if unknown."""
    code = (report_type_code or "SAR").strip().upper()
    if code not in REGISTRY:
        raise KeyError(f"Unknown report_type_code: {report_type_code!r}. Supported: {list(REGISTRY.keys())}")
    return REGISTRY[code]


def get_required_input_keys(report_type_code: str) -> frozenset[str]:
    return get_report_type_spec(report_type_code).required_input_keys


def has_local_fallback(report_type_code: str) -> bool:
    """True if this report type has local narrative guidance when KB has no row."""
    spec = get_report_type_spec(report_type_code)
    return bool(spec.local_instructions or spec.local_examples_text)


def get_report_type_from_input(data: dict[str, Any]) -> str:
    """Return report_type_code from input (report_type_code or report_type) or default SAR."""
    raw = data.get("report_type_code") or data.get("report_type")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().upper()
    return "SAR"
