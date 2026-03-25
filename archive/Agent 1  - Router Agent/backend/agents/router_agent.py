import json

from crewai import Agent, Task
from crewai import LLM


def create_router_agent(llm: LLM, tools: list) -> Agent:
    return Agent(
        role="Report Type Classifier",
        goal="Accurately determine which report(s) are required (SAR, CTR, or BOTH) based on transaction data",
        backstory="""You are an expert compliance analyst with 15 years of experience 
        in banking regulations. You specialize in identifying suspicious activity patterns 
        and determining the appropriate regulatory reporting requirements. You understand 
        the Bank Secrecy Act, FinCEN requirements, and OFAC sanctions regulations intimately.

        Your expertise includes:
        - Recognizing structuring patterns (multiple transactions just under $10,000)
        - Determining when CTR filing is required (cash transactions >= $10,000)
        - Distinguishing between standard threshold reporting and suspicious behavior

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
        Analyze the following transaction data and classify filing requirement.

        Transaction Data:
        {json.dumps(transaction_data, indent=2)}

        Output a JSON object with:
        - report_types: ["SAR"], ["CTR"], ["CTR","SAR"], or []
        - confidence_score: float between 0 and 1
        - total_cash_amount: float
        - reasoning: concise factual explanation
        - kb_status: "EXISTS" or "MISSING"
        - narrative_description: 2-4 factual sentences

        Classification policy:
        - SAR when suspicious activity indicators exist
        - CTR when total cash activity >= 10,000
        - BOTH when both conditions apply
        - [] when neither applies

        Return JSON only.
        """,
        expected_output="""{
"report_types": ["CTR", "SAR"],
"confidence_score": 0.95,
"total_cash_amount": 15500.0,
"reasoning": "Cash exceeds threshold and suspicious indicators are present",
"kb_status": "EXISTS",
"narrative_description": "Natural language description of the pattern"
}""",
        agent=agent,
    )
