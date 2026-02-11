import json

from crewai import Agent, Task
from crewai import LLM


def create_narrative_agent(llm: LLM, tools: list) -> Agent:
    """
    Agent 4: Compliance Narrative Writer
    """

    return Agent(
        role="Expert Compliance Narrative Writer",
        goal="Write clear, professional, and regulatory-compliant narratives that fully explain suspicious activity following the 'who, what, when, where, why, how' structure",
        backstory="""You are a former federal bank examiner turned compliance officer with 20 years of experience writing Suspicious Activity Reports. You've written over 5,000 SAR narratives that have been used in successful prosecutions.

        Your writing is characterized by:
        - Crystal-clear explanations that anyone can understand
        - Precise factual details (dates, amounts, locations)
        - Professional, objective tone without speculation
        - Logical flow from observation to conclusion
        - Proper citation of regulatory violations

        You follow the sacred structure: Who, What, When, Where, Why, How.
        You write in a way that would impress both regulators and prosecutors.""",
        tools=tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )


def create_narrative_task(agent: Agent, aggregator_output: dict) -> Task:
    return Task(
        description=f"""
        Write a professional SAR narrative based on this case summary.

        Case Summary:
        {json.dumps(aggregator_output, indent=2)}

        Steps:
        1. Use "Search Knowledge Base" to find 5 similar high-quality narrative examples
        2. Study the style and structure of those examples
        3. Write a narrative that covers who, what, when, where, why, how
        4. Keep length between 200-5000 characters
        5. Be factual, specific, and professional
        6. Cite relevant regulations where applicable
        """,
        expected_output="""JSON object with:
    {
        "narrative_text": "Full narrative here...",
        "word_count": 247,
        "character_count": 1432,
        "key_points_covered": ["who", "what", "when", "where", "why", "how"],
        "regulations_cited": ["31 USC 5324", "BSA"]
    }""",
        agent=agent,
    )
