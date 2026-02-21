import json
from typing import List, Optional

from crewai import Agent, Task
from crewai import LLM

from backend.tools.pdf_tools import (
    fill_ctr_pdf_tool,
    fill_report_pdf_tool,
    fill_sar_pdf_tool,
    generate_report_pdf_tool,
)


def create_filer_agent(llm: LLM, tools: list | None = None) -> Agent:
    """Agent 6: generate final compliance filing PDFs (SAR/CTR/BOTH)."""
    resolved_tools = tools or [
        fill_sar_pdf_tool,
        fill_ctr_pdf_tool,
        fill_report_pdf_tool,
        generate_report_pdf_tool,
    ]

    return Agent(
        role="Compliance Report Filing Specialist",
        goal=(
            "Generate properly formatted compliance PDFs (SAR, CTR, or both) with all "
            "fields correctly filled and ready for submission."
        ),
        backstory=(
            "You are a compliance operations specialist who has filed thousands of SARs and CTRs. "
            "You always call the right PDF tool for the requested report type and return complete "
            "JSON including pdf_path, report_id, status, and report_type."
        ),
        tools=resolved_tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=5,
    )


def create_filer_task(
    agent: Agent,
    case_json_path: Optional[str] = None,
    report_types: Optional[List[str]] = None,
    aggregator_output: Optional[dict] = None,
    narrative_output: Optional[dict] = None,
    validation_output: Optional[dict] = None,
) -> Task:
    """
    Create a filing task.

    Supports both styles:
    - New: case_json_path + report_types
    - Legacy: aggregator_output/narrative_output/validation_output
    """
    report_types = report_types or ["SAR"]
    if case_json_path:
        if len(report_types) > 1:
            instruction = f'Use "Fill Report PDF" with case_json_path="{case_json_path}" and report_type="BOTH".'
        elif "CTR" in report_types:
            instruction = f'Use "Fill CTR PDF Form" with case_json_path="{case_json_path}".'
        else:
            instruction = f'Use "Fill SAR PDF Form" with case_json_path="{case_json_path}".'
    else:
        case_data_json = json.dumps(aggregator_output or {}, ensure_ascii=False)
        narrative_text = (
            (narrative_output or {}).get("narrative")
            or (narrative_output or {}).get("text")
            or ""
        )
        report_type = report_types[0] if report_types else "SAR"
        instruction = (
            'Use "Generate Report PDF" with:\n'
            f"  - case_data: {case_data_json}\n"
            f"  - narrative: {narrative_text}\n"
            f'  - report_type: "{report_type}"'
        )

    return Task(
        description=f"""
        Generate the final compliance filing PDF(s).

        Requested report type(s): {", ".join(report_types)}

        {instruction}

        Validation context:
        {json.dumps(validation_output or {}, indent=2)}

        Confirm output status is "success" and include pdf_path (or reports list if BOTH).
        Return the full result JSON.
        """,
        expected_output="""{
  "status": "success",
  "report_id": "<uuid>",
  "case_id": "<case_id>",
  "report_type": "SAR" | "CTR",
  "pdf_path": "data/output/<TYPE>_<case_id>_<id>.pdf",
  "fields_filled": <integer>,
  "generated_at": "<iso_timestamp>"
}
OR
{
  "status": "success",
  "reports": [ { ... CTR ... }, { ... SAR ... } ]
}""",
        agent=agent,
    )
