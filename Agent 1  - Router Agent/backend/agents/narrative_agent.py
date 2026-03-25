"""Agent 4 narrative generation using Supabase-hosted guidance/examples."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

import requests
from crewai import Agent, Crew, LLM, Process, Task
from loguru import logger

from backend.config.settings import settings


class NarrativeKnowledgeBaseError(RuntimeError):
    """Raised when narrative guidance cannot be loaded from Supabase."""


def _validate_input(data: Dict[str, Any]) -> Dict[str, Any]:
    required = {"case_id", "subject", "SuspiciousActivityInformation"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"Input missing required keys: {sorted(missing)}")
    return data


def _supabase_rest_url() -> str:
    url = settings.get_supabase_rest_url()
    if not url:
        raise NarrativeKnowledgeBaseError(
            "SUPABASE_URL must be an https:// URL for narrative generation."
        )
    return url.rstrip("/") + "/rest/v1"


def _supabase_headers() -> Dict[str, str]:
    key = (settings.SUPABASE_ANON_KEY or "").strip()
    if not key:
        raise NarrativeKnowledgeBaseError("SUPABASE_ANON_KEY is required for narrative generation.")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _supabase_get(table: str, params: Dict[str, str]) -> List[Dict[str, Any]]:
    url = f"{_supabase_rest_url()}/{table}"
    response = requests.get(url, headers=_supabase_headers(), params=params, timeout=20)
    if not response.ok:
        raise NarrativeKnowledgeBaseError(
            f"Failed to fetch {table}: {response.status_code} {response.text}"
        )
    payload = response.json()
    if isinstance(payload, list):
        return payload
    return []


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return value
    return value


def _fetch_report_type_row(report_type_code: str) -> Dict[str, Any]:
    code = (report_type_code or "SAR").upper()
    attempts = [
        {
            "select": "report_type,narrative_required,narrative_instructions,json_schema",
            "report_type": f"eq.{code}",
            "limit": "1",
        },
        {
            "select": "report_type,narrative_instructions,json_schema",
            "report_type": f"eq.{code}",
            "limit": "1",
        },
        {
            "select": "report_type_code,narrative_required,narrative_instructions,json_schema",
            "report_type_code": f"eq.{code}",
            "limit": "1",
        },
        {
            "select": "report_type_code,narrative_instructions,json_schema",
            "report_type_code": f"eq.{code}",
            "limit": "1",
        },
    ]

    last_exc: Exception | None = None
    for params in attempts:
        try:
            rows = _supabase_get("report_types", params)
        except NarrativeKnowledgeBaseError as exc:
            # Supabase 400 often means requested column does not exist.
            last_exc = exc
            continue
        if rows:
            return rows[0]

    raise NarrativeKnowledgeBaseError(
        f"No compatible report_types row found for {code}. Last error: {last_exc}"
    )


def _fetch_narrative_examples(report_type_code: str) -> List[Dict[str, Any]]:
    code = (report_type_code or "SAR").upper()
    attempts = [
        {
            "select": "summary,narrative_text,effectiveness_notes,example_order",
            "report_type": f"eq.{code}",
            "order": "example_order.asc.nullslast",
            "limit": "8",
        },
        {
            "select": "summary,narrative_text,effectiveness_notes,example_order",
            "report_type_code": f"eq.{code}",
            "order": "example_order.asc.nullslast",
            "limit": "8",
        },
        {
            "select": "summary,narrative_text,effectiveness_notes,example_order",
            "order": "example_order.asc.nullslast",
            "limit": "8",
        },
    ]

    for params in attempts:
        try:
            rows = _supabase_get("narrative_examples", params)
        except NarrativeKnowledgeBaseError:
            continue
        if rows:
            return rows
    return []


def _build_guidance_context(report_type_code: str) -> Tuple[str, str]:
    row = _fetch_report_type_row(report_type_code)

    instruction_parts: List[str] = []
    instructions = (row.get("narrative_instructions") or "").strip()
    if instructions:
        instruction_parts.append(instructions)

    schema = _coerce_json(row.get("json_schema")) or {}
    if isinstance(schema, dict):
        guidance = ((schema.get("part_V") or {}).get("narrative_guidance") or [])
        if isinstance(guidance, list) and guidance:
            instruction_parts.append("Additional narrative guidance from schema:")
            instruction_parts.extend([f"- {item}" for item in guidance if item])

    examples_rows = _fetch_narrative_examples(report_type_code)
    example_chunks: List[str] = []
    for idx, row in enumerate(examples_rows, 1):
        summary = (row.get("summary") or "").strip()
        text = (row.get("narrative_text") or "").strip()
        if not text:
            continue
        if summary:
            example_chunks.append(f"--- Example {idx}: {summary} ---")
        else:
            example_chunks.append(f"--- Example {idx} ---")
        example_chunks.append(text)
        notes = (row.get("effectiveness_notes") or "").strip()
        if notes:
            example_chunks.append("Why effective:")
            example_chunks.append(notes)
        example_chunks.append("")

    instructions_text = "\n\n".join(instruction_parts).strip()
    examples_text = "\n".join(example_chunks).strip()
    return instructions_text, examples_text


def _build_task_description(input_json: Dict[str, Any], report_type_code: str) -> str:
    instructions_text, examples_text = _build_guidance_context(report_type_code)
    input_str = json.dumps(input_json, indent=2)
    return f"""You are generating the mandatory narrative section for a {report_type_code} report.
You must NOT hallucinate or invent any information. Use ONLY facts from the input JSON.

Narrative instructions:
{instructions_text}

Reference examples:
{examples_text}

Current input JSON:
{input_str}

Output requirements:
1) Write one concise narrative paragraph based only on the current input.
2) Return only a JSON object with a single key "narrative".
3) No markdown, no extra text.
Example: {{"narrative": "Narrative text..."}}"""


def _parse_narrative_output(raw_output: str) -> str:
    text = (raw_output or "").strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        text = match.group(0)
    data = json.loads(text)
    narrative = data.get("narrative")
    if not isinstance(narrative, str) or not narrative.strip():
        raise ValueError("Narrative output missing required 'narrative' text.")
    return narrative.strip()


def generate_narrative_payload(
    input_data: Dict[str, Any],
    report_type_code: str = "SAR",
    *,
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    Generate narrative payload for downstream validator/filer.

    Returns:
      {
        "narrative_text": "...",
        "word_count": int,
        "character_count": int,
        "key_points_covered": ["who","what","when","where","why","how"],
        "regulations_cited": [...]
      }
    """
    _validate_input(input_data)
    report_code = (report_type_code or "SAR").upper()

    llm = LLM(
        model="openai/gpt-4o-mini",
        temperature=0.2,
        max_tokens=2200,
    )
    agent = Agent(
        role="SAR Narrative Writer",
        goal="Write accurate, factual narratives using only the provided suspicious activity data.",
        backstory=(
            "You are a compliance analyst drafting SAR narratives. You never invent "
            "names, dates, amounts, accounts, or events not explicitly provided."
        ),
        llm=llm,
        verbose=verbose,
        allow_delegation=False,
    )
    task = Task(
        description=_build_task_description(input_data, report_code),
        expected_output="JSON object with key 'narrative' only.",
        agent=agent,
    )
    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=verbose,
    )
    result = crew.kickoff()
    raw_output = str(result)
    if hasattr(result, "tasks_output") and result.tasks_output:
        raw_output = getattr(result.tasks_output[-1], "raw", str(result.tasks_output[-1]))
    elif hasattr(result, "raw"):
        raw_output = result.raw

    narrative = _parse_narrative_output(raw_output)
    words = [w for w in re.split(r"\s+", narrative) if w]
    cited = sorted(set(re.findall(r"\b\d+\s+(?:USC|CFR)\s+\d+(?:\.\d+)?\b", narrative)))
    output = {
        "narrative_text": narrative,
        "narrative": narrative,
        "word_count": len(words),
        "character_count": len(narrative),
        "key_points_covered": ["who", "what", "when", "where", "why", "how"],
        "regulations_cited": cited,
    }
    logger.debug("Generated narrative payload for case {}", input_data.get("case_id"))
    return output
