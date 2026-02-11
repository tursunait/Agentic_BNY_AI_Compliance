import json

from crewai import Agent, Task
from crewai import LLM


def create_researcher_agent(llm: LLM, tools: list) -> Agent:
    """
    Agent 2: Regulatory Intelligence Specialist
    """

    return Agent(
        role="Regulatory Intelligence and Research Specialist",
        goal="Locate official regulatory guidance, extract report structures and requirements, and update the knowledge base with accurate, current information",
        backstory="""You are a regulatory research specialist with expertise in navigating government websites and extracting structured information from complex regulatory documents. You've worked with FinCEN, OFAC, and federal banking regulators for 10 years.

        Your skills:
        - Expert at navigating FinCEN.gov, Treasury.gov, OFAC websites
        - Can parse PDF regulations and extract requirements
        - Understand how to structure regulatory data for KB storage
        - Know where to find official, authoritative sources

        You never use unofficial sources - only .gov sites and official publications.""",
        tools=tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )


def create_researcher_task(agent: Agent, router_output: dict) -> Task:
    return Task(
        description=f"""
        The knowledge base does not have information for {router_output.get('report_type')} reports. Find and add this information.

        Steps:
        1. Use "Scrape FinCEN Website" to find guidance for {router_output.get('report_type')}
        2. Use "Download Regulatory PDF" if needed to get full documentation
        3. Extract:
           - Report structure (all required fields)
           - Field definitions and requirements
           - Validation rules
           - Filing deadlines and procedures
        4. Use "Add to Knowledge Base" to store:
           - Report schema in regulations collection
           - Any example language in narratives collection
        5. Confirm successful KB update

        Only use official .gov sources.
        """,
        expected_output="""JSON object with:
    {
        "kb_updated": true,
        "items_added": {
            "regulations": ["reg-id-1", "reg-id-2"],
            "schemas": ["schema-id-1"]
        },
        "sources": [
            "https://www.fincen.gov/...",
            "https://www.treasury.gov/..."
        ],
        "summary": "Added complete SAR filing requirements from FinCEN guidance dated 2024-01-15"
    }""",
        agent=agent,
    )
