import json

from crewai import Agent, Task
from crewai import LLM


def create_aggregator_agent(llm: LLM, tools: list) -> Agent:
    """
    Agent 3: Data Mapper

    Role: Map transaction data to report schema structure
    Goal: Create comprehensive, structured case summary
    """

    return Agent(
        role="Data Mapper and Case Analyst",
        goal="Transform raw transaction data into a structured case summary that maps perfectly to the compliance report schema, identifying all risk indicators and missing information",
        backstory="""You are a senior compliance data analyst specializing in 
        Suspicious Activity Reports. You have a photographic memory for report 
        schemas and can instantly map any transaction data to the correct fields.

        Your expertise:
        - Deep knowledge of SAR, CTR, and sanctions report structures
        - Pattern recognition for suspicious activity indicators
        - Understanding of regulatory requirements for each field
        - Ability to identify what information is missing vs. optional

        You work with meticulous attention to detail, ensuring every piece of 
        information is placed in the correct field and all risk indicators are 
        properly flagged.""",
        tools=tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )


def create_aggregator_task(agent: Agent, router_output: dict) -> Task:
    description = f"""
    Map the transaction data to the {router_output.get('report_type', 'unknown')} report schema 
    and create a comprehensive case summary.

    Input from Router:
    {json.dumps(router_output, indent=2)}

    Steps:
    1. Use "Get Report Schema" to retrieve the schema for {router_output.get('report_type')}
    2. Use "Get Field Mappings" to understand how to map data
    3. Use "Search Knowledge Base" to find similar cases for reference
    4. Map each piece of transaction data to appropriate schema fields
    5. Identify suspicious activity indicators/risk factors
    6. Flag any missing required information
    7. Calculate a completeness score

    Be thorough and precise in your mapping.
    """

    expected = """JSON object with:
    {
        "subject_info": {
            "name": "...",
            "account_number": "...",
            "ssn": "..."
        },
        "transaction_details": [
            {
                "date": "...",
                "amount": 0.00,
                "type": "..."
            }
        ],
        "risk_indicators": ["Structuring", "Multiple branches"],
        "missing_fields": ["beneficiary_name"],
        "completeness_score": 0.85,
        "total_amount": 112400.00,
        "transaction_count": 12,
        "timespan_days": 10
    }"""

    return Task(
        description=description,
        expected_output=expected,
        agent=agent,
    )
