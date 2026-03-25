"""
Supabase REST client for router_agent using your table structure:
- report_types (report_type_code, json_schema, narrative_instructions, ...)
- required_fields (report_type_code, input_key, field_label, is_required, ask_user_prompt, ...)
Uses SUPABASE_URL and SUPABASE_ANON_KEY from backend.config.settings.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests
from loguru import logger

from backend.config.settings import settings


REQUEST_TIMEOUT_SECONDS = 5
_REST_DISABLED_REASON: Optional[str] = None


def _disable_rest(reason: str) -> None:
    global _REST_DISABLED_REASON
    if _REST_DISABLED_REASON is None:
        _REST_DISABLED_REASON = reason
        logger.warning("Supabase REST disabled for this process: {}", reason)


def _rest_enabled() -> bool:
    if _REST_DISABLED_REASON:
        return False
    url = (settings.get_supabase_rest_url() or "").strip()
    key = (getattr(settings, "SUPABASE_ANON_KEY", None) or "").strip()
    return bool(url.startswith(("http://", "https://")) and key)


def _is_dns_error(exc: Exception) -> bool:
    msg = str(exc)
    markers = (
        "NameResolutionError",
        "Failed to resolve",
        "nodename nor servname provided",
        "Temporary failure in name resolution",
        "socket.gaierror",
    )
    return any(marker in msg for marker in markers)


def _headers() -> Dict[str, str]:
    key = (getattr(settings, "SUPABASE_ANON_KEY", None) or "").strip()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _request_json(url: str, params: Dict[str, str]) -> List[Dict[str, Any]]:
    try:
        r = requests.get(url, headers=_headers(), params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        r.raise_for_status()
    except requests.RequestException as exc:
        if _is_dns_error(exc):
            _disable_rest(f"dns_unavailable: {exc}")
        raise
    payload = r.json()
    return payload if isinstance(payload, list) else []


def fetch_report_type_row(report_type_code: str) -> Optional[Dict[str, Any]]:
    """
    Fetch one row from report_types for SAR/CTR.

    Supports either column naming convention:
    - report_type_code
    - report_type
    """
    if not _rest_enabled():
        return None
    code = str(report_type_code).strip().upper()
    url = settings.get_supabase_rest_url().rstrip("/")
    full_url = f"{url}/rest/v1/report_types"

    attempts = [
        {
            "select": "id,report_type_code,display_name,narrative_required,narrative_instructions,json_schema,validation_rules,pdf_template_path,pdf_field_mapping,is_active",
            "report_type_code": f"eq.{code}",
            "limit": "1",
        },
        {
            "select": "id,report_type,narrative_required,narrative_instructions,json_schema,validation_rules,pdf_template_path,pdf_field_mapping,is_active",
            "report_type": f"eq.{code}",
            "limit": "1",
        },
        {
            "select": "json_schema,narrative_required,is_active,report_type_code",
            "report_type_code": f"eq.{code}",
            "limit": "1",
        },
        {
            "select": "json_schema,narrative_required,is_active,report_type",
            "report_type": f"eq.{code}",
            "limit": "1",
        },
    ]

    last_error: Exception | None = None
    for params in attempts:
        try:
            rows = _request_json(full_url, params)
            if rows:
                return rows[0]
        except requests.HTTPError as exc:
            last_error = exc
            # Schema mismatch (column not found) should fall through to next shape.
            status = getattr(exc.response, "status_code", None)
            if status == 400:
                continue
            break
        except Exception as exc:
            last_error = exc
            break

    logger.warning("fetch_report_type_row {} failed: {}", code, last_error or "not found")
    return None


def fetch_required_fields(report_type_code: str, required_only: bool = True) -> List[Dict[str, Any]]:
    """
    Fetch rows from required_fields for the given report type code.

    Supports either required_fields.report_type_code or required_fields.report_type.
    """
    if not _rest_enabled():
        return []
    code = str(report_type_code).strip().upper()
    url = settings.get_supabase_rest_url().rstrip("/")
    full_url = f"{url}/rest/v1/required_fields"

    attempts = [
        {
            "select": "id,report_type_code,field_number,field_label,part,field_type,is_required,input_key,conditional_note,ask_user_prompt",
            "report_type_code": f"eq.{code}",
            "order": "id.asc",
        },
        {
            "select": "id,report_type,field_number,field_label,part,field_type,is_required,input_key,conditional_note,ask_user_prompt",
            "report_type": f"eq.{code}",
            "order": "id.asc",
        },
    ]
    if required_only:
        for params in attempts:
            params["is_required"] = "eq.true"

    last_error: Exception | None = None
    for params in attempts:
        try:
            rows = _request_json(full_url, params)
            return rows
        except requests.HTTPError as exc:
            last_error = exc
            status = getattr(exc.response, "status_code", None)
            if status == 400:
                continue
            break
        except Exception as exc:
            last_error = exc
            break

    logger.warning("fetch_required_fields {} failed: {}", code, last_error or "not found")
    return []


def get_required_input_keys(report_type_code: str) -> List[str]:
    """
    Return list of input_key values from required_fields for this report type (is_required=true).
    Used as required field paths for validation.
    """
    rows = fetch_required_fields(report_type_code, required_only=True)
    out = []
    for row in rows:
        if isinstance(row, dict) and row.get("input_key"):
            out.append(str(row["input_key"]).strip())
    return sorted(set(out))


def get_required_fields_with_prompts(report_type_code: str) -> List[Dict[str, Any]]:
    """
    Return required fields (is_required=true) with ask_user_prompt and field_label
    for multi-turn collection of missing values. Each item: input_key, ask_user_prompt, field_label.
    """
    rows = fetch_required_fields(report_type_code, required_only=True)
    out = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("input_key"):
            continue
        out.append(
            {
                "input_key": str(row["input_key"]).strip(),
                "ask_user_prompt": str(row.get("ask_user_prompt") or "").strip()
                or row.get("field_label")
                or row["input_key"],
                "field_label": str(row.get("field_label") or row["input_key"]).strip(),
            }
        )
    return out


def get_report_type_schema(report_type_code: str) -> Optional[Dict[str, Any]]:
    """
    Return json_schema from report_types for this report_type_code, or None if not found.
    """
    row = fetch_report_type_row(report_type_code)
    if not row:
        return None
    raw = row.get("json_schema")
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None
