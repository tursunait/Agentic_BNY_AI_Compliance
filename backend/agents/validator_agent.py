import json

from crewai import Agent, Task
from crewai import LLM


def create_validator_agent(llm: LLM, tools: list) -> Agent:
    """
    Agent 5: Quality Assurance Officer
    """

    return Agent(
        role="Compliance Quality Assurance Officer",
        goal="Thoroughly validate that the compliance report meets all regulatory requirements, contains all necessary information, and meets quality standards before filing",
        backstory="""You are a former FinCEN examiner who now leads quality assurance for a major bank's compliance department. You have reviewed over 10,000 SARs and have an encyclopedic knowledge of what regulators look for.

        You are known for:
        - Catching errors others miss
        - Deep knowledge of BSA, AML, and OFAC requirements
        - Understanding of what makes a narrative "audit-proof"
        - Ability to assess both technical compliance and practical quality

        You use a three-tier validation approach:
        1. Technical validation (required fields & formatting)
        2. Regulatory compliance (BSA/FinCEN alignment)
        3. Quality assessment (clarity, specificity, tone)

        You are tough but fair - you'll approve only flawless work.""",
        tools=tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )


def create_validator_task(agent: Agent, aggregator_output: dict, narrative_output: dict) -> Task:
    return Task(
        description=f"""
        Validate this compliance report for completeness, regulatory compliance, and quality.

        Case Summary:
        {json.dumps(aggregator_output, indent=2)}

        Narrative:
        {json.dumps(narrative_output, indent=2)}

        Steps:
        1. Use "Get Validation Rules" to retrieve all rules for this report type
        2. Check completeness: ensure required fields are populated and score completeness
        3. Check regulatory compliance: confirm alignment with BSA/FinCEN obligations
        4. Check narrative quality: clarity, completeness, tone, specificity, length
        5. Assign final status (APPROVED, NEEDS_REVIEW, REJECTED)
        6. Provide actionable feedback
        """,
        expected_output="""JSON object with:
    {
        "status": "APPROVED" | "NEEDS_REVIEW" | "REJECTED",
        "completeness_score": 95.0,
        "compliance_checks": {
            "required_fields": "PASS",
            "bsa_compliance": "PASS",
            "fincen_guidelines": "PASS"
        },
        "narrative_quality_score": 8.5,
        "narrative_quality_breakdown": {...},
        "issues": [...],
        "recommendations": [...],
        "approval_flag": true
    }""",
        agent=agent,
    )
