"""
CrewAI Router Agent: classifies compliance report type from natural language or JSON input.
Uses OpenAI GPT-4.1 mini via CrewAI LLM.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from crewai import Agent, Task, Crew, Process, LLM

from backend.config.settings import settings
from router_agent.config import ROUTER_LLM_MODEL

# Optional: tool to fetch schema so agent can mention what's in KB (we do KB check in run.py)
from backend.tools.kb_tools import get_schema_tool


def create_router_llm() -> LLM:
    return LLM(
        model=ROUTER_LLM_MODEL,
        temperature=0.0,
        max_tokens=2000,
        api_key=settings.OPENAI_API_KEY,
    )


def create_router_agent(llm: LLM, tools: List[Any] | None = None) -> Agent:
    tools = tools or []
    return Agent(
        role="Compliance Report Router",
        goal="Determine which compliance report type the user wants to file (SAR, CTR, Sanctions, or BOTH) from their description or structured data.",
        backstory="""You are an expert compliance analyst. You read the compliance officer's request—
        whether in plain language (e.g. 'I need to file a SAR for this suspicious wire') or structured JSON—
        and identify the correct report type: SAR (Suspicious Activity), CTR (Currency Transaction), Sanctions, or BOTH when both SAR and CTR apply.""",
        tools=tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=2,
    )


def _task_description(user_input: Any) -> str:
    if isinstance(user_input, dict):
        return f"""
The user has provided structured (JSON) data. Infer the required report type from the data and any explicit report_type field.

Structured input:
{json.dumps(user_input, indent=2, default=str)}

Return a JSON object with: report_type, confidence_score (0-1), reasoning (short).
report_type must be one of: SAR, CTR, SANCTIONS, BOTH, OTHER.
"""
    return f"""
The user has provided the following request in natural language:

"{user_input}"

Determine which compliance report type they need: SAR (Suspicious Activity Report), CTR (Currency Transaction Report), Sanctions report, or BOTH.
Return a JSON object with: report_type, confidence_score (0-1), reasoning (short).
report_type must be one of: SAR, CTR, SANCTIONS, BOTH, OTHER.
Return only valid JSON, no markdown.
"""


def create_router_task(agent: Agent, user_input: Any) -> Task:
    return Task(
        description=_task_description(user_input),
        expected_output="""A JSON object with keys: report_type (one of SAR, CTR, SANCTIONS, BOTH, OTHER), confidence_score (number 0-1), reasoning (string). No other text.""",
        agent=agent,
    )


def classify_report_type(user_input: str | Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the CrewAI router agent to classify report type from user input.
    Returns dict with report_type, confidence_score, reasoning (and optional raw_parsed).
    """
    llm = create_router_llm()
    agent = create_router_agent(llm, tools=[get_schema_tool])
    task = create_router_task(agent, user_input)
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=True)
    result = crew.kickoff()

    # Parse JSON from result
    raw = result
    if hasattr(raw, "raw"):
        raw = getattr(raw, "raw", raw)
    if isinstance(raw, str):
        try:
            # Strip markdown code block if present
            text = raw.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                text = "\n".join(lines)
            raw = json.loads(text)
        except json.JSONDecodeError:
            raw = {"report_type": "OTHER", "confidence_score": 0.0, "reasoning": "Could not parse agent output."}
    if not isinstance(raw, dict):
        raw = {"report_type": "OTHER", "confidence_score": 0.0, "reasoning": "Invalid agent output."}

    report_type = str(raw.get("report_type", "OTHER")).upper()
    if report_type not in {"SAR", "CTR", "SANCTIONS", "BOTH", "OTHER"}:
        report_type = "OTHER"
    return {
        "report_type": report_type,
        "confidence_score": float(raw.get("confidence_score", 0.0)),
        "reasoning": str(raw.get("reasoning", "")),
        "raw_parsed": raw,
    }
