"""AI-powered field verification for compliance PDF forms.

After `SARFieldMapper` / `CTRFieldMapper` produces the `field_values` dict,
call `verify_and_correct_fields()` to run a lightweight GPT-4o review that
compares the mapped values against the source case data and returns a
corrected copy of the dict.

Only human-readable text fields (names, institution, narrative, amount text)
are checked — digit-cell encodings (Text2/Text7/etc.) are excluded because
they require positional logic the LLM cannot verify.

The function is fail-safe: any error during the LLM call returns the original
field_values unchanged so PDF generation always continues.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from loguru import logger


# ---------------------------------------------------------------------------
# Field-set definitions: which field IDs to surface to the LLM reviewer
# Keys   = exact AcroForm field IDs (as written to the PDF)
# Values = human-readable label used in the LLM prompt
# ---------------------------------------------------------------------------

_SAR_ACROFORM_TEXT_FIELDS: Dict[str, str] = {
    "3  Individuals last name or entitys legal name a Unk": "subject_last_name_or_entity",
    "4  First name": "subject_first_name",
    "5  Middle name": "subject_middle_name",
    "76  Financial institution name where activity occurred": "institution_name",
    "78  Primary federal regulator": "primary_federal_regulator",
    "Narrative": "narrative",
}

_SAR_LEGACY_TEXT_FIELDS: Dict[str, str] = {
    "item15": "subject_last_name",
    "item16": "subject_first_name",
    "item2": "institution_name",
    "item51": "narrative",
}

_CTR_TEXT_FIELDS: Dict[str, str] = {
    "f1-68": "institution_name",
    "f1-97": "preparer_name",
    "f1-98": "preparer_title",
}


# ---------------------------------------------------------------------------
# Source-data extractor
# ---------------------------------------------------------------------------

def _extract_source_summary(case_data: Dict[str, Any], report_type: str) -> Dict[str, str]:
    """Pull human-readable ground-truth fields from the source case dict."""
    subject = case_data.get("subject") or {}
    inst = case_data.get("institution") or {}

    raw_name = str(subject.get("name") or "").strip()
    parts = raw_name.split()
    last = parts[-1] if parts else ""
    first = parts[0] if len(parts) > 1 else ""
    middle = parts[1] if len(parts) > 2 else ""

    # Amount
    sai = case_data.get("SuspiciousActivityInformation") or {}
    amount_info = sai.get("26_AmountInvolved") or {}
    amount_raw = (
        amount_info.get("amount_usd")
        or case_data.get("total_amount_involved")
        or 0.0
    )
    try:
        amount_str = f"${float(amount_raw):,.2f}"
    except (TypeError, ValueError):
        amount_str = str(amount_raw)

    # Activity date range
    date_range = sai.get("27_DateOrDateRange") or {}
    date_from = str(date_range.get("from") or date_range.get("start") or "")
    date_to = str(date_range.get("to") or date_range.get("end") or "")

    return {
        "case_id": str(case_data.get("case_id") or ""),
        "report_type": report_type,
        "subject_last_name": last,
        "subject_first_name": first,
        "subject_middle_name": middle,
        "subject_full_name": raw_name,
        "institution_name": str(inst.get("name") or ""),
        "primary_federal_regulator": str(inst.get("primary_federal_regulator") or ""),
        "amount": amount_str,
        "activity_date_from": date_from,
        "activity_date_to": date_to,
    }


# ---------------------------------------------------------------------------
# Mapped-field extractor — only the fields we want the LLM to review
# ---------------------------------------------------------------------------

def _extract_mapped_summary(
    field_values: Dict[str, str],
    report_type: str,
    template_variant: str = "",
) -> Dict[str, str]:
    """Return a {field_id: value} subset limited to reviewable text fields."""
    if report_type == "CTR":
        field_map = _CTR_TEXT_FIELDS
    elif template_variant == "fincen_acroform":
        field_map = _SAR_ACROFORM_TEXT_FIELDS
    else:
        field_map = _SAR_LEGACY_TEXT_FIELDS

    result: Dict[str, str] = {}
    for field_id, label in field_map.items():
        # Only include fields the mapper actually set — skipping absent fields
        # prevents the LLM from "correcting" values it can't write back anyway.
        if field_id not in field_values:
            continue
        val = str(field_values[field_id])
        # Truncate long values (narrative) so the prompt stays compact
        if len(val) > 600:
            val = val[:600] + "…[truncated]"
        result[field_id] = val
    return result


# ---------------------------------------------------------------------------
# Verification prompt
# ---------------------------------------------------------------------------

_VERIFICATION_PROMPT = """\
You are a compliance QA reviewer for a FinCEN {report_type} filing system.

## Source data (ground truth)
{source_json}

## Mapped PDF field values (what will be written to the form)
Field IDs and their current values:
{mapped_json}

## Your task
1. Compare each mapped field value against the source data.
2. Flag any field that is clearly wrong or is empty/blank when the source has a
   non-empty value for the same concept.
3. For the "Narrative" or "item51" field: verify it mentions the correct
   subject name, the amount ({amount}), and is not a generic placeholder.
4. Do NOT change the narrative text style — only correct factual errors or
   fill a blank narrative if the source has enough information.
5. Do NOT alter digit-cell fields (Text2, Text7, Text8, Text9, Text10).

Return ONLY a JSON object mapping the exact field_id (no labels, no extra text)
to the corrected string value for every field that needs fixing.
Return {{}} if all mapped values look correct.
No explanation outside the JSON.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def verify_and_correct_fields(
    field_values: Dict[str, str],
    case_data: Dict[str, Any],
    report_type: str,
    template_variant: str = "",
    llm=None,
) -> Dict[str, str]:
    """
    Run an LLM review of *field_values* against *case_data* and return a
    corrected copy.  Falls back to the original dict on any error.

    Parameters
    ----------
    field_values:
        The dict produced by SARFieldMapper / CTRFieldMapper.
    case_data:
        Normalized case dict (source of truth).
    report_type:
        "SAR" or "CTR".
    template_variant:
        "fincen_acroform" or "legacy" (SAR only).
    llm:
        Optional pre-built LLM instance.  If None, a GPT-4o client is created
        automatically using settings.OPENAI_API_KEY.
    """
    try:
        from openai import OpenAI
        from backend.config.settings import settings

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
    except Exception as exc:
        logger.warning("AI field verifier: could not initialise OpenAI client — skipping. {}", exc)
        return field_values

    source_summary = _extract_source_summary(case_data, report_type)
    mapped_summary = _extract_mapped_summary(field_values, report_type, template_variant)

    if not mapped_summary:
        logger.debug("AI field verifier: no reviewable fields for variant={} — skipping.", template_variant)
        return field_values

    prompt = _VERIFICATION_PROMPT.format(
        report_type=report_type,
        source_json=json.dumps(source_summary, indent=2),
        mapped_json=json.dumps(mapped_summary, indent=2),
        amount=source_summary.get("amount", "unknown"),
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0.0,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.warning("AI field verifier: LLM call failed — skipping corrections. {}", exc)
        return field_values

    # Strip markdown code fences if present
    if response_text.startswith("```"):
        response_text = response_text[response_text.index("\n") + 1:] if "\n" in response_text else response_text[3:]
    if response_text.endswith("```"):
        response_text = response_text[: response_text.rfind("```")].rstrip()
    response_text = response_text.strip()

    try:
        corrections: Any = json.loads(response_text)
    except json.JSONDecodeError as exc:
        logger.warning("AI field verifier: could not parse LLM response as JSON — skipping. {}: {!r}", exc, response_text[:200])
        return field_values

    if not isinstance(corrections, dict) or not corrections:
        logger.debug("AI field verifier: no corrections returned.")
        return field_values

    corrected = dict(field_values)
    applied = 0
    for field_id, new_val in corrections.items():
        if field_id in corrected:
            old_val = corrected[field_id]
            corrected[field_id] = str(new_val)
            logger.info(
                "AI field verifier [{} / {}] corrected '{}': {!r} → {!r}",
                report_type, template_variant, field_id, old_val[:80], str(new_val)[:80],
            )
            applied += 1
        else:
            logger.debug("AI field verifier: correction for unknown field '{}' ignored.", field_id)

    logger.info("AI field verifier: {} correction(s) applied to {} fields.", applied, len(field_values))
    return corrected
