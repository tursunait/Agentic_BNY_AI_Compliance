import json
from typing import Dict

from crewai import Crew, Process, LLM

from backend.tools.kb_tools import (
    search_kb_tool,
    get_schema_tool,
    get_validation_rules_tool,
    convert_to_narrative_tool,
    get_field_mappings_tool,
    add_to_kb_tool,
)
from backend.tools.web_scraper import scrape_fincen_tool, download_regulation_tool
from backend.tools.pdf_tools import CTRReportFiler, SARReportFiler
from backend.tools.field_mapper import calculate_total_cash_amount, determine_report_types, normalize_case_data
from backend.config.settings import settings
from backend.agents.router_agent import create_router_agent, create_router_task
from backend.agents.researcher_agent import create_researcher_agent, create_researcher_task
from backend.agents.aggregator_agent import create_aggregator_agent, create_aggregator_task
from backend.agents.narrative_agent import create_narrative_agent, create_narrative_task
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


def create_compliance_crew(transaction_data: dict) -> Dict[str, dict]:
    normalized_case = normalize_case_data(transaction_data)
    base_llm = LLM(model="gpt-4.1", temperature=0.1, max_tokens=4000, api_key=settings.OPENAI_API_KEY)
    narrative_llm = LLM(model="gpt-4.1", temperature=0.4, max_tokens=2000, api_key=settings.OPENAI_API_KEY)

    router_agent = create_router_agent(
        llm=base_llm,
        tools=[search_kb_tool, get_schema_tool, convert_to_narrative_tool],
    )
    router_task = create_router_task(router_agent, normalized_case)
    router_crew = Crew(agents=[router_agent], tasks=[router_task], process=Process.sequential, verbose=True)

    router_result = router_crew.kickoff()
    router_output = _parse_jsonish(router_result)
    report_types = determine_report_types(normalized_case)
    router_output["report_types"] = report_types
    router_output["total_cash_amount"] = calculate_total_cash_amount(normalized_case)
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

    if router_output.get("kb_status") == "MISSING":
        researcher_agent = create_researcher_agent(llm=base_llm, tools=[scrape_fincen_tool, download_regulation_tool, add_to_kb_tool])
        researcher_task = create_researcher_task(researcher_agent, router_output)
        researcher_crew = Crew(agents=[researcher_agent], tasks=[researcher_task], process=Process.sequential, verbose=True)
        researcher_crew.kickoff()

    validation_output: Dict[str, dict]
    if "SAR" in report_types:
        aggregator_agent = create_aggregator_agent(
            llm=base_llm,
            tools=[get_schema_tool, get_field_mappings_tool, search_kb_tool, get_validation_rules_tool],
        )
        narrative_agent = create_narrative_agent(llm=narrative_llm, tools=[search_kb_tool])
        validator_agent = create_validator_agent(llm=base_llm, tools=[get_validation_rules_tool, search_kb_tool])

        aggregator_task = create_aggregator_task(aggregator_agent, router_output)
        narrative_task = create_narrative_task(narrative_agent, {})
        validator_task = create_validator_task(validator_agent, {}, {})

        main_crew = Crew(
            agents=[aggregator_agent, narrative_agent, validator_agent],
            tasks=[aggregator_task, narrative_task, validator_task],
            process=Process.sequential,
            verbose=True,
        )

        main_result = main_crew.kickoff()
        validation_output = _parse_jsonish(main_result)
    else:
        # CTR-only path: threshold report can be filed without SAR narrative/validation.
        validation_output = {
            "approval_flag": True,
            "status": "APPROVED",
            "message": "CTR threshold condition met; SAR pipeline skipped.",
        }

    final_output: Dict[str, dict]
    if validation_output.get("approval_flag"):
        # Deterministic filing avoids LLM-output parsing risk for final artifacts.
        reports = []
        if "CTR" in report_types:
            reports.append(CTRReportFiler().fill_from_dict(normalized_case))
        if "SAR" in report_types:
            reports.append(SARReportFiler().fill_from_dict(normalized_case))
        final_output = reports[0] if len(reports) == 1 else {"status": "success", "reports": reports}
    else:
        final_output = {
            "status": "needs_review",
            "validation_report": validation_output,
            "message": "Report did not pass validation - human review required",
        }

    return {
        "router": router_output,
        "validation": validation_output,
        "final": final_output,
    }
