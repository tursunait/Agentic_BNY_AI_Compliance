"""Supabase-backed knowledge base for narrative configuration and examples.

This module replaces hardcoded narrative instructions and examples with data
retrieved from the Supabase knowledge base:

- report_types: core configuration per report type (e.g., SAR)
- narrative_examples: style and effectiveness examples per report type

Configuration is provided via environment variables:

- SUPABASE_URL: base Supabase URL, e.g. https://ggxnbctgyiitfwxharjt.supabase.co
- SUPABASE_ANON_KEY: anon / service role key with read access to the KB
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, List, Optional

import requests

from narrative_agent.report_types import get_report_type_spec, has_local_fallback


class KnowledgeBaseError(RuntimeError):
    """Raised when the Supabase knowledge base cannot be reached or used."""


@dataclass
class ReportTypeConfig:
    """Configuration for a narrative report type (e.g., SAR)."""

    report_type_code: str
    display_name: Optional[str] = None
    regulatory_body: Optional[str] = None
    narrative_required: Optional[bool] = None
    narrative_instructions: Optional[str] = None
    json_schema: Optional[dict[str, Any]] = None
    validation_rules: Optional[dict[str, Any]] = None
    pdf_template_path: Optional[str] = None
    pdf_field_mapping: Optional[dict[str, Any]] = None


@dataclass
class NarrativeExample:
    """One narrative example for a given report type."""

    summary: str
    narrative_text: str
    effectiveness_notes: Optional[str] = None
    example_order: Optional[int] = None


def _get_supabase_base_url() -> str:
    url = os.getenv("SUPABASE_URL")
    if not url:
        raise KnowledgeBaseError(
            "SUPABASE_URL is not set. Please configure your Supabase project URL."
        )
    return url.rstrip("/") + "/rest/v1"


def _get_supabase_headers() -> dict[str, str]:
    api_key = os.getenv("SUPABASE_ANON_KEY")
    if not api_key:
        raise KnowledgeBaseError(
            "SUPABASE_ANON_KEY is not set. Please configure your Supabase anon key."
        )
    return {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def fetch_report_type_config(report_type_code: str) -> ReportTypeConfig:
    """Fetch the report_types row for the given report_type_code from Supabase.

    The query mirrors:
    /rest/v1/report_types?select=*&narrative_required=eq.TRUE&report_type_code=eq.SAR
    but with a configurable report_type_code.
    """
    base_url = _get_supabase_base_url()
    headers = _get_supabase_headers()
    params = {
        "select": "*",
        "narrative_required": "eq.TRUE",
        "report_type_code": f"eq.{report_type_code}",
    }
    resp = requests.get(f"{base_url}/report_types", headers=headers, params=params, timeout=15)
    if not resp.ok:
        raise KnowledgeBaseError(
            f"Failed to fetch report_types for {report_type_code}: "
            f"{resp.status_code} {resp.text}"
        )
    rows: List[dict[str, Any]] = resp.json()
    if not rows:
        raise KnowledgeBaseError(
            f"No active narrative-enabled report_type found for code={report_type_code!r}."
        )
    row = rows[0]
    return ReportTypeConfig(
        report_type_code=row.get("report_type_code", report_type_code),
        display_name=row.get("display_name"),
        regulatory_body=row.get("regulatory_body"),
        narrative_required=row.get("narrative_required"),
        narrative_instructions=row.get("narrative_instructions"),
        json_schema=row.get("json_schema") or None,
        validation_rules=row.get("validation_rules") or None,
        pdf_template_path=row.get("pdf_template_path"),
        pdf_field_mapping=row.get("pdf_field_mapping") or None,
    )


def fetch_narrative_examples(report_type_code: str) -> list[NarrativeExample]:
    """Fetch narrative examples for a given report type, ordered by example_order."""
    base_url = _get_supabase_base_url()
    headers = _get_supabase_headers()
    params = {
        "select": "*",
        "report_type_code": f"eq.{report_type_code}",
        "order": "example_order.asc",
    }
    resp = requests.get(
        f"{base_url}/narrative_examples", headers=headers, params=params, timeout=15
    )
    if not resp.ok:
        raise KnowledgeBaseError(
            f"Failed to fetch narrative_examples for {report_type_code}: "
            f"{resp.status_code} {resp.text}"
        )
    rows: List[dict[str, Any]] = resp.json()
    examples: list[NarrativeExample] = []
    for row in rows:
        summary = row.get("summary")
        narrative_text = row.get("narrative_text")
        if not summary or not narrative_text:
            # Skip malformed rows rather than failing the whole call.
            continue
        examples.append(
            NarrativeExample(
                summary=summary,
                narrative_text=narrative_text,
                effectiveness_notes=row.get("effectiveness_notes"),
                example_order=row.get("example_order"),
            )
        )
    return examples


def _get_local_guidance(report_type_code: str) -> tuple[str, str]:
    """Return (instructions_text, examples_text) from the report type registry (local fallback)."""
    spec = get_report_type_spec(report_type_code)
    instructions_text = spec.local_instructions or ""
    examples_text = spec.local_examples_text or ""
    return instructions_text, examples_text


def build_narrative_guidance_context(
    report_type_code: str,
) -> tuple[str, str]:
    """Return (instructions_text, examples_text) for a given report type.

    Tries Supabase first (report_types.narrative_instructions + narrative_examples).
    If the KB has no row for this report type and the type has local fallback
    (e.g. OFAC_REJECT), returns local guidance so retrieval + generation
    still works without Supabase.
    """
    try:
        cfg = fetch_report_type_config(report_type_code)
        instructions_lines: list[str] = []
        if cfg.narrative_instructions:
            instructions_lines.append(cfg.narrative_instructions.strip())

        schema = cfg.json_schema or {}
        part_v = schema.get("part_V") or {}
        narrative_guidance = part_v.get("narrative_guidance") or []
        if narrative_guidance:
            instructions_lines.append("Additional narrative guidance from the schema:")
            for item in narrative_guidance:
                instructions_lines.append(f"- {item}")

        instructions_text = "\n\n".join(instructions_lines).strip()

        examples = fetch_narrative_examples(report_type_code)
        example_lines: list[str] = []
        for i, ex in enumerate(examples, 1):
            example_lines.append(f"--- Example {i} ({ex.summary}) ---")
            example_lines.append("Example narrative text:")
            example_lines.append(ex.narrative_text.strip())
            if ex.effectiveness_notes:
                example_lines.append("Why this example is effective:")
                example_lines.append(ex.effectiveness_notes.strip())
            example_lines.append("")

        examples_text = "\n".join(example_lines).strip()
        return instructions_text, examples_text
    except KnowledgeBaseError:
        if has_local_fallback(report_type_code):
            return _get_local_guidance(report_type_code)
        raise

