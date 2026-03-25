"""
Knowledge Base client for the router: check report type existence and fetch schema/required fields.
- When Supabase REST is enabled (SUPABASE_URL + SUPABASE_ANON_KEY): uses your tables
  report_types (report_type_code) and required_fields (input_key, is_required, ask_user_prompt).
- Otherwise: uses backend KBManager (Postgres report_schemas or legacy REST).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from router_agent import supabase_rest
from router_agent.schema_validator import get_required_field_paths as schema_required_paths

# Fallback when Supabase REST not used
from backend.knowledge_base.kb_manager import KBManager

_kb: Optional[KBManager] = None
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LOCAL_SCHEMA_PATHS = {
    "SAR": _PROJECT_ROOT / "knowledge_base" / "schemas" / "sar_schema.json",
    "CTR": _PROJECT_ROOT / "knowledge_base" / "schemas" / "ctr_schema.json",
}


def _get_kb() -> KBManager:
    global _kb
    if _kb is None:
        _kb = KBManager()
    return _kb


def _use_supabase_rest() -> bool:
    return supabase_rest._rest_enabled()


def _load_local_schema(report_type: str) -> Optional[Dict[str, Any]]:
    path = _LOCAL_SCHEMA_PATHS.get(str(report_type or "").strip().upper())
    if not path or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        logger.warning("Failed to read local schema {}: {}", path, exc)
        return None


def report_type_exists(report_type: str) -> bool:
    """
    Check if the given report type exists in the Knowledge Base.
    Uses Supabase report_types.report_type_code when REST is enabled, else KBManager.
    """
    if not (report_type and str(report_type).strip()):
        return False
    rt = str(report_type).strip().upper()
    if _use_supabase_rest():
        row = supabase_rest.fetch_report_type_row(rt)
        if row is not None:
            return row.get("is_active") is not False
        logger.warning(
            "Supabase report_types lookup returned no row for {}. Falling back to local schema.",
            rt,
        )

    try:
        if _load_local_schema(rt):
            return True
        _get_kb().get_schema(rt)
        return True
    except ValueError as e:
        logger.debug("Report type {} not in KB: {}", rt, e)
        return False
    except Exception as e:
        logger.warning("KB check failed for {}: {}", rt, e)
        return False


def get_report_schema(report_type: str) -> Dict[str, Any]:
    """
    Fetch the JSON schema for the report type.
    Uses Supabase report_types.json_schema (report_type_code) when REST enabled, else KBManager.
    Raises ValueError if report type is not found.
    """
    if not (report_type and str(report_type).strip()):
        raise ValueError("report_type is required")
    rt = str(report_type).strip().upper()
    if _use_supabase_rest():
        schema = supabase_rest.get_report_type_schema(rt)
        if isinstance(schema, dict) and schema:
            return schema
        logger.warning(
            "Supabase schema lookup returned empty for {}. Falling back to local schema.",
            rt,
        )

    local_schema = _load_local_schema(rt)
    if isinstance(local_schema, dict) and local_schema:
        return local_schema

    return _get_kb().get_schema(rt)


def get_required_field_paths(report_type: str) -> List[str]:
    """
    Return required field paths for this report type for validation.
    - When Supabase REST is enabled: uses required_fields table where is_required=TRUE.
      Falls back to schema-derived required paths if the table is missing/unavailable.
    - Otherwise: uses report schema (from KBManager/local schema) and schema_validator to derive paths.
    """
    if not (report_type and str(report_type).strip()):
        return []
    rt = str(report_type).strip().upper()
    if _use_supabase_rest():
        keys = supabase_rest.get_required_input_keys(rt)
        if keys:
            return keys
        logger.warning(
            "Supabase required_fields lookup returned empty for {}. Falling back to schema-derived requirements.",
            rt,
        )
    schema = get_report_schema(rt)
    return schema_required_paths(schema, rt)


def get_required_fields_with_prompts(report_type: str) -> List[Dict[str, Any]]:
    """
    Return required fields with ask_user_prompt for multi-turn collection (is_required=TRUE only).
    When Supabase is enabled: from required_fields table. If unavailable, synthesize prompts from
    schema-derived required paths.

    Each item: input_key, ask_user_prompt, field_label.
    """
    if not (report_type and str(report_type).strip()):
        return []
    rt = str(report_type).strip().upper()
    if _use_supabase_rest():
        prompts = supabase_rest.get_required_fields_with_prompts(rt)
        if prompts:
            return prompts

    return [
        {
            "input_key": path,
            "ask_user_prompt": f"Please provide value for {path}",
            "field_label": path,
        }
        for path in get_required_field_paths(rt)
    ]
