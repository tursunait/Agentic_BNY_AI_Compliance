"""
CrewAI Narrative Generator Agent for regulatory narratives (SAR, OFAC_REJECT, etc.).
Generates the narrative section from structured input using KB-retrieved or local guidance.
"""

import json
import re
from typing import Any

from crewai import Agent, Crew, LLM, Process, Task

from narrative_agent.knowledge_base import (
    KnowledgeBaseError,
    build_narrative_guidance_context,
)
from narrative_agent.report_types import get_report_type_from_input, get_report_type_spec
from narrative_agent.schemas import NarrativeOutput, validate_input


def _build_task_description(
    input_json: dict[str, Any],
    report_type_code: str = "SAR",
) -> str:
    """Build the task description from KB (or local fallback) for the given report type.

    Retrieves narrative instructions and examples for report_type_code, then
    injects the structured input. Used by create_crew for the task description.
    """
    spec = get_report_type_spec(report_type_code)
    instructions_text, examples_text = build_narrative_guidance_context(
        report_type_code
    )
    reference = instructions_text or ""
    few_shot = examples_text or ""

    input_str = json.dumps(input_json, indent=2)
    return f"""You are generating the mandatory narrative section for a {report_type_code} report. You must NOT hallucinate or invent any information. Use ONLY the data provided below.

{reference}

Few-shot examples and reference narratives. Follow this style and use ONLY facts from the input:

{few_shot}

---

Current input ({spec.prompt_input_label}) — use ONLY this data to write the narrative:

{input_str}

---

Generate exactly one narrative paragraph based solely on the current input above. Then output your response as a single JSON object with one key "narrative" whose value is that paragraph. No other keys. Example format: {{"narrative": "Your paragraph here."}}"""


def _parse_narrative_output(raw: str) -> NarrativeOutput:
    """Extract JSON from agent output and validate."""
    raw = raw.strip()
    # Try to find a JSON object in the output (in case of markdown or extra text)
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        raw = match.group(0)
    data = json.loads(raw)
    return NarrativeOutput(**data)


def create_crew(
    input_json: dict[str, Any],
    report_type_code: str = "SAR",
    *,
    verbose: bool = True,
) -> Crew:
    """Create a CrewAI crew for one-shot narrative generation.

    Args:
        input_json: Full input JSON for narrative generation.
        report_type_code: Code for the report type (e.g., "SAR", "OFAC_REJECT").
            Used to fetch narrative instructions and examples from the knowledge base
            (or local fallback) and to set agent role/goal/backstory.
        verbose: Whether to print CrewAI execution logs.
    """
    spec = get_report_type_spec(report_type_code)
    llm = LLM(
        model="openai/gpt-4o-mini",
        temperature=0.2,
        max_tokens=2000,
    )
    agent = Agent(
        role=spec.agent_role,
        goal=spec.agent_goal,
        backstory=spec.agent_backstory,
        llm=llm,
        verbose=verbose,
    )
    task = Task(
        description=_build_task_description(input_json, report_type_code=report_type_code),
        expected_output=f"A JSON object with a single key 'narrative' containing the {spec.expected_output_hint}. No other text.",
        agent=agent,
    )
    return Crew(
        agents=[agent], tasks=[task], process=Process.sequential, verbose=verbose
    )


def generate_narrative(
    input_data: dict[str, Any],
    report_type_code: str | None = None,
    *,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    Generate narrative from structured input. Report type routes to the correct
    KB retrieval and generation flow.

    Args:
        input_data: Full input JSON. For SAR: case_id, subject, SuspiciousActivityInformation, etc.
            For OFAC_REJECT: case_id, transaction, case_facts, etc. May include report_type_code.
        report_type_code: Report type (e.g., "SAR", "OFAC_REJECT"). If None, read from
            input_data["report_type_code"] or input_data["report_type"] (default "SAR").
        verbose: Whether to print CrewAI execution logs.

    Returns:
        Same structure as input with one additional key "narrative" (str).
    """
    if report_type_code is None:
        report_type_code = get_report_type_from_input(input_data)
    validate_input(input_data, report_type_code=report_type_code)
    crew = create_crew(input_data, report_type_code=report_type_code, verbose=verbose)
    result = crew.kickoff()
    # CrewAI returns CrewOutput; get last task's raw output
    raw_output = str(result)
    if hasattr(result, "tasks_output") and result.tasks_output:
        last = result.tasks_output[-1]
        raw_output = getattr(last, "raw", str(last))
    elif hasattr(result, "raw"):
        raw_output = result.raw
    parsed = _parse_narrative_output(raw_output)
    # Return exact same structure as input with one new field "narrative" (shallow copy + add key)
    out = dict(input_data)
    out["narrative"] = parsed.narrative
    return out


class NarrativeGeneratorCrew:
    """
    Convenience class to run the narrative generator with optional custom LLM.
    """

    def __init__(self, *, verbose: bool = True):
        self.verbose = verbose

    def kickoff(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Run the crew and return the narrative output. inputs = SAR input JSON."""
        return generate_narrative(inputs, verbose=self.verbose)
