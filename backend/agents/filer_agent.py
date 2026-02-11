import json

from crewai import Agent, Task
from crewai import LLM


def create_filer_agent(llm: LLM, tools: list) -> Agent:
    """
    Agent 6: Report Generator
    """

    return Agent(
        role="Compliance Report Filing Specialist",
        goal="Generate a properly formatted PDF compliance report with all data correctly mapped to form fields and ready for regulatory submission",
        backstory="""You are a compliance operations specialist who has filed over 50,000 regulatory reports. You know every field of the SAR, CTR, and sanctions report forms by heart.

        You are obsessive about:
        - Correct field mapping (right data in right boxes)
        - Proper formatting (dates, amounts, text wrapping)
        - Complete metadata (generated timestamp, report ID)
        - PDF compliance (readable, fillable, printable)

        You take pride in creating pixel-perfect reports that pass automated validation systems on the first try.""",
        tools=tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )


def create_filer_task(agent: Agent, aggregator_output: dict, narrative_output: dict, validation_output: dict) -> Task:
    return Task(
        description=f"""
        Generate the final PDF compliance report.

        Case Data:
        {json.dumps(aggregator_output, indent=2)}

        Narrative:
        {json.dumps(narrative_output, indent=2)}

        Validation Report:
        {json.dumps(validation_output, indent=2)}

        Steps:
        1. Use "Generate Report PDF" tool with:
           - Case data from Aggregator
           - Narrative from Narrative Agent
           - Report type from Router
        2. The tool will:
           - Load the correct PDF template
           - Fill all fields with mapped data
           - Insert narrative in proper section
           - Add metadata footer (report ID, generated timestamp)
        3. Save to /tmp/reports/[report_id].pdf
        4. Return the file path and metadata
        """,
        expected_output="""JSON object with:
    {
        "report_id": "uuid-here",
        "pdf_path": "/tmp/reports/uuid-here.pdf",
        "file_size_kb": 245,
        "page_count": 8,
        "report_type": "SAR",
        "generated_at": "2025-02-07T10:30:00Z",
        "metadata": {
            "agent_version": "1.0",
            "total_processing_time_seconds": 45
        }
    }""",
        agent=agent,
    )
