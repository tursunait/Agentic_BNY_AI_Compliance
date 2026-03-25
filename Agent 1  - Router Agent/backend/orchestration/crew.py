import json
from typing import Any, Callable, Dict

from crewai import Crew, Process, LLM

from backend.tools.kb_tools import (
    search_kb_tool,
    get_validation_rules_tool,
)
from backend.tools.pdf_tools import CTRReportFiler, SARReportFiler
from backend.tools.field_mapper import (
    calculate_total_cash_amount,
    determine_report_types,
    has_suspicious_activity,
    normalize_case_data,
)
from backend.config.settings import settings
from backend.agents.aggregator_agent import AggregatorOrchestrator
from backend.agents.router_agent import create_router_agent, create_router_task
from backend.agents.narrative_agent import generate_narrative_payload
from backend.agents.validator_agent import create_validator_agent, create_validator_task


def _parse_jsonish(payload) -> Dict:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {}
    raw = getattr(payload, "raw", None)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def _build_router_reasoning(total_cash_amount: float, suspicious: bool, report_types: list[str]) -> str:
    if not report_types:
        return "No suspicious indicators and cash amount below filing threshold."
    reasons = []
    if "CTR" in report_types:
        reasons.append(f"total cash amount is ${total_cash_amount:,.2f} (>= $10,000)")
    if "SAR" in report_types and suspicious:
        reasons.append("suspicious activity indicators are present")
    if len(report_types) == 2:
        return "Both report types required because " + " and ".join(reasons) + "."
    if report_types[0] == "CTR":
        return "CTR required because " + " and ".join(reasons) + "."
    return "SAR required because " + " and ".join(reasons) + "."


def _build_narrative_input(normalized_case: Dict[str, Any], sar_aggregate: Dict[str, Any]) -> Dict[str, Any]:
    """Build Agent 4 input payload with required keys."""
    output = dict(normalized_case)
    output["case_id"] = sar_aggregate.get("case_id") or output.get("case_id")

    subject = output.get("subject")
    if not isinstance(subject, dict) or not subject:
        subject = {
            "subject_id": sar_aggregate.get("customer_id"),
            "name": sar_aggregate.get("customer_name"),
        }
    output["subject"] = subject

    suspicious_info = output.get("SuspiciousActivityInformation")
    if not isinstance(suspicious_info, dict) or not suspicious_info:
        suspicious_info = sar_aggregate.get("SuspiciousActivityInformation")
    if not isinstance(suspicious_info, dict):
        suspicious_info = {
            "26_AmountInvolved": {"amount_usd": sar_aggregate.get("total_amount_involved", 0.0), "no_amount": False},
            "27_DateOrDateRange": {
                "from": (sar_aggregate.get("activity_date_range") or {}).get("start"),
                "to": (sar_aggregate.get("activity_date_range") or {}).get("end"),
            },
            "35_OtherSuspiciousActivities": sar_aggregate.get("suspicious_activity_type", []),
        }
    output["SuspiciousActivityInformation"] = suspicious_info
    return output


def _normalize_report_types(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
            raw = parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            raw = []
    else:
        raw = []

    out: list[str] = []
    for item in raw:
        report_type = str(item or "").upper()
        if report_type in {"SAR", "CTR"} and report_type not in out:
            out.append(report_type)
    return out


def create_compliance_crew(
    transaction_data: dict,
    on_stage: Callable[[str, int], None] | None = None,
) -> Dict[str, dict]:
    normalized_case = normalize_case_data(transaction_data)
    base_llm = LLM(model="gpt-4.1", temperature=0.1, max_tokens=4000, api_key=settings.OPENAI_API_KEY)

    def mark_stage(agent: str, progress: int) -> None:
        if on_stage is None:
            return
        try:
            on_stage(agent, progress)
        except Exception:
            pass

    router_output: Dict[str, Any] = {}
    try:
        # Agent 1 (Router) executed via CrewAI; tools intentionally disabled to
        # prevent tool-loop failures and keep routing stable.
        mark_stage("router", 15)
        router_agent = create_router_agent(llm=base_llm, tools=[])
        router_task = create_router_task(router_agent, normalized_case)
        router_crew = Crew(
            agents=[router_agent],
            tasks=[router_task],
            process=Process.sequential,
            verbose=True,
        )
        router_result = router_crew.kickoff()
        router_output = _parse_jsonish(router_result)
    except Exception as exc:
        router_output = {"router_error": str(exc)}

    total_cash_amount = calculate_total_cash_amount(normalized_case)
    suspicious = has_suspicious_activity(normalized_case)
    report_types = _normalize_report_types(router_output.get("report_types"))
    if not report_types:
        report_types = determine_report_types(normalized_case)
    router_output["report_types"] = report_types
    router_output["total_cash_amount"] = total_cash_amount
    router_output["reasoning"] = _build_router_reasoning(total_cash_amount, suspicious, report_types)
    router_output.setdefault("confidence_score", 1.0 if report_types else 0.0)
    router_output.setdefault("kb_status", "EXISTS")
    if report_types:
        # Keep legacy key for downstream prompts expecting one report type.
        router_output["report_type"] = "SAR" if "SAR" in report_types else report_types[0]
    else:
        router_output["report_type"] = "NONE"

    if not report_types:
        return {
            "router": router_output,
            "validation": {
                "approval_flag": False,
                "status": "NO_FILING_REQUIRED",
                "message": "No CTR or SAR requirement detected for this case.",
            },
            "final": {
                "status": "no_filing_required",
                "message": "No CTR or SAR filing requirements met",
            },
        }

    # Researcher (Agent 2) intentionally skipped per workflow requirement.

    aggregator = AggregatorOrchestrator(llm=base_llm)
    aggregated_by_type: Dict[str, Dict[str, Any]] = {}
    mark_stage("aggregator", 35)
    for report_type in report_types:
        aggregated = aggregator.process(
            raw_data=normalized_case,
            report_type=report_type,
            case_id=normalized_case.get("case_id") if isinstance(normalized_case, dict) else None,
        )
        aggregated_by_type[report_type] = aggregated.model_dump(mode="json")

    primary_report_type = "SAR" if "SAR" in aggregated_by_type else report_types[0]
    aggregator_output: Dict[str, Any] = aggregated_by_type[primary_report_type]

    narrative_output: Dict[str, Any] = {}
    sar_aggregate = aggregated_by_type.get("SAR")
    if isinstance(sar_aggregate, dict) and sar_aggregate.get("narrative_required", True):
        mark_stage("narrative", 55)
        narrative_input = _build_narrative_input(normalized_case, sar_aggregate)
        narrative_output = generate_narrative_payload(
            narrative_input,
            report_type_code="SAR",
            verbose=True,
        )

    mark_stage("validator", 75)
    if settings.SKIP_VALIDATOR_FOR_TESTING:
        validation_output = {
            "status": "APPROVED",
            "approval_flag": True,
            "compliance_checks": {"validator": "SKIPPED_FOR_TESTING"},
            "issues": [],
            "recommendations": ["Validator was bypassed for testing mode."],
            "skip_reason": "SKIP_VALIDATOR_FOR_TESTING=true",
        }
    else:
        validator_agent = create_validator_agent(llm=base_llm, tools=[get_validation_rules_tool, search_kb_tool])
        validator_task = create_validator_task(validator_agent, aggregator_output, narrative_output)
        validator_crew = Crew(
            agents=[validator_agent],
            tasks=[validator_task],
            process=Process.sequential,
            verbose=True,
        )
        validator_result = validator_crew.kickoff()
        validation_output = _parse_jsonish(validator_result)
        if "approval_flag" not in validation_output:
            status = str(validation_output.get("status", "")).upper()
            validation_output["approval_flag"] = status == "APPROVED"
        if "status" not in validation_output:
            validation_output["status"] = "APPROVED" if validation_output.get("approval_flag") else "NEEDS_REVIEW"

    final_output: Dict[str, dict]
    if validation_output.get("approval_flag"):
        # Deterministic filing avoids LLM-output parsing risk for final artifacts.
        mark_stage("filer", 90)
        reports = []
        if "CTR" in report_types:
            reports.append(CTRReportFiler().fill_from_dict(normalized_case))
        if "SAR" in report_types:
            sar_case = dict(normalized_case)
            narrative_text = (
                narrative_output.get("narrative_text")
                or narrative_output.get("narrative")
                or narrative_output.get("text")
            )
            if narrative_text:
                sar_case["narrative"] = narrative_text
            reports.append(SARReportFiler().fill_from_dict(sar_case))
        final_output = reports[0] if len(reports) == 1 else {"status": "success", "reports": reports}
    else:
        final_output = {
            "status": "needs_review",
            "validation_report": validation_output,
            "message": "Report did not pass validation - human review required",
        }

    return {
        "router": router_output,
        "aggregator": aggregator_output,
        "aggregator_by_type": aggregated_by_type,
        "narrative": narrative_output,
        "validation": validation_output,
        "final": final_output,
    }
