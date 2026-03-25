"""
Fetch report template + field mapping from Supabase `report_types`.

Table uses `report_type_code` (and optionally display names). Callers may pass
`report_type_name` per pipeline contract; we resolve aliases (e.g. SANCTIONS_REJECT → OFAC_REJECT).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
from dotenv import load_dotenv
from loguru import logger

# Pipeline / product names → DB report_type_code
REPORT_TYPE_ALIASES: dict[str, str] = {
    "SANCTIONS_REJECT": "OFAC_REJECT",
    "SANCTIONS_REJECTION": "OFAC_REJECT",
    "OFAC": "OFAC_REJECT",
    "OFAC_REJECT": "OFAC_REJECT",
}


def _supabase_rest_config() -> tuple[str, str]:
    load_dotenv()
    base = (os.environ.get("SUPABASE_URL") or "").strip().strip('"').strip("'").rstrip("/")
    key = (
        os.environ.get("SUPABASE_API_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or ""
    )
    key = key.strip().strip('"').strip("'")
    if not base or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_API_KEY (or SUPABASE_ANON_KEY) must be set in the environment."
        )
    return base, key


def normalize_report_type_code(name_or_code: str) -> str:
    """Map user-facing report type to Supabase report_type_code."""
    raw = (name_or_code or "").strip().upper()
    if not raw:
        raise ValueError("report_type_name is required")
    return REPORT_TYPE_ALIASES.get(raw, raw)


def fetch_report_type_row(report_type_name: str) -> dict[str, Any]:
    """
    Load one row from report_types for the given type name/code.

    Returns dict with: report_type_code, pdf_template_path, pdf_field_mapping (dict).
    """
    base, key = _supabase_rest_config()
    code = normalize_report_type_code(report_type_name)
    url = f"{base}/rest/v1/report_types"
    params = {"report_type_code": f"eq.{code}", "select": "report_type_code,pdf_template_path,pdf_field_mapping"}
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }
    with httpx.Client(timeout=60.0) as client:
        r = client.get(url, params=params, headers=headers)
        r.raise_for_status()
        rows = r.json()
    if not rows:
        # Retry without alias (exact code as stored)
        with httpx.Client(timeout=60.0) as client:
            r = client.get(
                url,
                params={
                    "report_type_code": f"eq.{report_type_name.strip().upper()}",
                    "select": "report_type_code,pdf_template_path,pdf_field_mapping",
                },
                headers=headers,
            )
            r.raise_for_status()
            rows = r.json()
    if not rows:
        raise LookupError(
            f"No report_types row for report_type_code={code!r} (from input {report_type_name!r}). "
            f"Add a row or extend REPORT_TYPE_ALIASES in backend/pdf_filler/metadata.py."
        )
    row = rows[0]
    mapping = row.get("pdf_field_mapping")
    if isinstance(mapping, str):
        try:
            row["pdf_field_mapping"] = json.loads(mapping)
        except json.JSONDecodeError as e:
            logger.error("pdf_field_mapping is not valid JSON: {}", e)
            row["pdf_field_mapping"] = {}
    if row["pdf_field_mapping"] is None:
        row["pdf_field_mapping"] = {}
    tpl = row.get("pdf_template_path") or ""
    if not tpl or not re.match(r"^https?://", str(tpl).strip(), re.I):
        raise ValueError(f"Invalid or missing pdf_template_path for {row.get('report_type_code')}")
    return row
