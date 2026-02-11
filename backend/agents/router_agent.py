import json

from crewai import Agent, Task
from crewai import LLM


def create_router_agent(llm: LLM, tools: list) -> Agent:
    return Agent(
        role="Report Type Classifier",
        goal="Accurately determine the type of compliance report needed (SAR, CTR, or Sanctions) based on transaction data",
        backstory="""You are an expert compliance analyst with 15 years of experience 
        in banking regulations. You specialize in identifying suspicious activity patterns 
        and determining the appropriate regulatory reporting requirements. You understand 
        the Bank Secrecy Act, FinCEN requirements, and OFAC sanctions regulations intimately.

        Your expertise includes:
        - Recognizing structuring patterns (multiple transactions just under $10,000)
        - Identifying sanctions violations (transactions to/from sanctioned entities)
        - Determining when CTR filing is required (cash transactions >= $10,000)
        - Distinguishing between different types of suspicious activity

        You are meticulous and never make classification errors, as the wrong report 
        type could lead to regulatory violations.""",
        tools=tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=3,
    )


def create_router_task(agent: Agent, transaction_data: dict) -> Task:
    return Task(
        description=f"""
        Analyze the following transaction data and determine the appropriate compliance report type.

        Transaction Data:
        {json.dumps(transaction_data, indent=2)}

        Steps:
        1. If the data is structured (JSON), use the "Convert Structured Data to Narrative" tool to create a narrative description
        2. Use the "Search Knowledge Base" tool to find similar cases
        3. Analyze the transaction pattern
        4. Determine if it's:
           - SAR (Suspicious Activity Report): Unusual patterns, structuring, fraud
           - CTR (Currency Transaction Report): Cash transactions >= $10,000
           - Sanctions: Transactions involving sanctioned entities/countries
        5. Check if we have the schema using "Get Report Schema"

        Provide:
        - Report type (SAR, CTR, or Sanctions)
        - Confidence score (0.0-1.0)
        - Brief reasoning
        - KB status (EXISTS or MISSING)
        - Narrative description
        """,
        expected_output="""{
"report_type": "SAR" | "CTR" | "Sanctions",
"confidence_score": 0.95,
"reasoning": "Brief explanation of why this report type was chosen",
"kb_status": "EXISTS" | "MISSING",
"narrative_description": "Natural language description of the pattern"
}""",
        agent=agent,
    )
