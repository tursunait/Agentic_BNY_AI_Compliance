"""
Download PDF templates and fill AcroForm fields using Supabase mapping rules.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from pypdf import PdfReader, PdfWriter

from backend.pdf_filler.sar_mapping import is_sar_new_mapping, parse_sar_mapping

# JSON path keys that bind to narrative_text / case narrative (not dotted data paths)
NARRATIVE_SOURCE_KEYS = frozenset({"narrative", "narrative_text", "__narrative__"})

# Checkbox on-state key in pypdf get_fields() result (internal use)
PDF_FIELD_STATES_KEY = "/_States_"
PDF_FIELD_STATES_ALT = "/States"


def _coerce_pdf_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(_coerce_pdf_value(x) for x in value if x is not None)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _get_nested(data: dict[str, Any], path: str) -> Any:
    """Resolve dot-separated path; supports dict traversal only."""
    if not path or not isinstance(data, dict):
        return None
    cur: Any = data
    for part in path.split("."):
        if not part:
            continue
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def _resolve_pdf_field_name(mapping_value: Any) -> str | None:
    """
    Supabase may store:
    - plain string (PDF field name)
    - {"field_id": "...", "type": "text", ...}
    """
    if mapping_value is None:
        return None
    if isinstance(mapping_value, str):
        s = mapping_value.strip()
        return s if s else None
    if isinstance(mapping_value, dict):
        fid = mapping_value.get("field_id") or mapping_value.get("pdf_field") or mapping_value.get("name")
        if fid and isinstance(fid, str):
            return fid.strip()
    return None


def _mapping_entries(pdf_field_mapping: dict[str, Any]) -> list[tuple[str, str]]:
    """(json_path_or_reserved_key, pdf_field_name) pairs."""
    out: list[tuple[str, str]] = []
    for json_key, spec in pdf_field_mapping.items():
        if json_key.startswith("_") and json_key not in NARRATIVE_SOURCE_KEYS:
            continue
        fname = _resolve_pdf_field_name(spec)
        if fname:
            out.append((str(json_key), fname))
    return out


def _resolve_source_value(
    json_key: str,
    data: dict[str, Any],
    narrative_text: str | None,
) -> Any:
    if json_key in NARRATIVE_SOURCE_KEYS:
        if narrative_text is not None and str(narrative_text).strip():
            return narrative_text
        return _get_nested(data, "narrative")
    v = _get_nested(data, json_key)
    if v is not None:
        return v
    # Shallow fallbacks for common layouts
    for prefix in ("transaction.", "institution.", "subject.", "case.", "preparer."):
        if json_key.startswith(prefix):
            break
    else:
        for prefix in ("transaction", "institution", "subject"):
            trial = f"{prefix}.{json_key}"
            v = _get_nested(data, trial)
            if v is not None:
                return v
    return None


def _normalize_space(s: str) -> str:
    return " ".join(s.strip().split())


def _match_pdf_field_name(pdf_name: str, pdf_field_names: set[str]) -> str | None:
    """Resolve mapping label to actual AcroForm name (trim / whitespace quirks)."""
    if pdf_name in pdf_field_names:
        return pdf_name
    s = pdf_name.strip()
    if s in pdf_field_names:
        return s
    norm = _normalize_space(pdf_name)
    for fn in pdf_field_names:
        if fn.strip() == s or _normalize_space(fn) == norm:
            return fn
    return None


def _narrative_fallback_field(existing_pdf_fields: dict[str, Any]) -> str | None:
    """Find a PDF field to hold narrative when mapping has no narrative entry."""
    candidates = []
    for name in existing_pdf_fields:
        lower = name.lower()
        if lower in ("narrative", "description", "additional information"):
            candidates.append(name)
        elif "narrative" in lower:
            candidates.append(name)
        elif lower == "page 2":
            candidates.append(name)
    # Prefer explicit narrative-like names over generic "Page 2"
    for c in candidates:
        if "narrative" in c.lower() or c.lower() in ("description", "additional information"):
            return c
    return candidates[0] if candidates else None


def download_template(url: str, dest_dir: Path | None = None) -> Path:
    dest_dir = dest_dir or Path(tempfile.gettempdir())
    dest_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix="pdf_filler_", dir=str(dest_dir))
    os.close(fd)
    path = Path(tmp)
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        path.write_bytes(r.content)
    logger.info("Downloaded PDF template ({} bytes) to {}", len(r.content), path)
    return path


def _get_checkbox_on_state(fields: dict[str, Any], pdf_field_name: str) -> str:
    """Return the 'on' state for a checkbox (e.g. /Yes, /On, /1). Default /Yes."""
    field_obj = None
    want = pdf_field_name.strip()
    for name, obj in (fields or {}).items():
        if name.strip() == want or name == pdf_field_name:
            field_obj = obj
            break
    if field_obj is None:
        return "/Yes"
    states = field_obj.get(PDF_FIELD_STATES_KEY) or field_obj.get(PDF_FIELD_STATES_ALT)
    if isinstance(states, (list, tuple)):
        for s in states:
            if s is None:
                continue
            ss = str(s).strip()
            if ss and ss.upper() not in ("/OFF", "OFF", "/0"):
                return ss if ss.startswith("/") else f"/{ss}"
    return "/Yes"


def build_sar_field_values(
    pdf_field_mapping: dict[str, Any],
    data: dict[str, Any],
    narrative_text: str | None,
    pdf_field_names: set[str],
    fields: dict[str, Any] | None,
) -> dict[str, str]:
    """
    Build field updates for SAR using new-format mapping (sections with type/label).
    Maps flatten keys (3, 1a, narrative_text, etc.) to PDF field names; checkboxes
    get the PDF's actual on-state so they render checked.
    """
    flat_specs, data_key_to_pdf, checkbox_field_names = parse_sar_mapping(pdf_field_mapping)
    updates: dict[str, str] = {}
    data = data or {}

    # Narrative: prefer param then data (narrative_text key then narrative)
    nt = (
        (narrative_text or "").strip()
        or _coerce_pdf_value(data.get("narrative_text") or data.get("narrative") or "").strip()
    )
    if nt:
        for dk in ("narrative_text", "narrative"):
            if dk in data_key_to_pdf:
                pdf_name = data_key_to_pdf[dk]
                actual = _match_pdf_field_name(pdf_name, pdf_field_names)
                if actual:
                    updates[actual] = nt
                    break
        else:
            # Fallback narrative field
            fb = _narrative_fallback_field({n: None for n in pdf_field_names})
            if fb:
                updates[fb] = nt

    for data_key, value in data.items():
        if data_key in NARRATIVE_SOURCE_KEYS or data_key == "narrative":
            continue
        pdf_name = data_key_to_pdf.get(data_key)
        if not pdf_name and "_" in str(data_key):
            pdf_name = data_key_to_pdf.get(str(data_key).split("_")[0])
        if not pdf_name:
            continue
        spec = flat_specs.get(pdf_name) or {}
        field_type = (spec.get("type") or "text").strip().lower()

        actual = _match_pdf_field_name(pdf_name, pdf_field_names)
        if not actual:
            continue

        if field_type == "checkbox":
            if value is None:
                continue
            if isinstance(value, str):
                low = value.strip().lower()
                if not low or low in ("no", "false", "off", "0"):
                    continue
            state = _get_checkbox_on_state(fields, actual) if fields else "/Yes"
            updates[actual] = state
        else:
            updates[actual] = _coerce_pdf_value(value)

    return updates


def build_field_values(
    pdf_field_mapping: dict[str, Any],
    data: dict[str, Any],
    narrative_text: str | None,
    pdf_field_names: set[str],
    *,
    sparse_fill: bool = False,
) -> dict[str, str]:
    """
    Map JSON → PDF field names; logs missing keys / missing PDF fields.

    When sparse_fill=True (SAR with partial flat keys), skip unprovided mapping keys
    without warning instead of logging hundreds of FinCEN field gaps.
    """
    updates: dict[str, str] = {}
    narrative_mapped = False
    data_keys = set(data.keys()) if isinstance(data, dict) else set()

    for json_key, pdf_name in _mapping_entries(pdf_field_mapping):
        if json_key in NARRATIVE_SOURCE_KEYS or json_key == "narrative":
            narrative_mapped = True
        if sparse_fill and json_key not in NARRATIVE_SOURCE_KEYS:
            if json_key not in data_keys:
                continue
        raw = _resolve_source_value(json_key, data, narrative_text)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if sparse_fill:
                continue
            logger.warning("Missing JSON value for mapped key {!r} → PDF field {!r}", json_key, pdf_name)
            continue
        actual = _match_pdf_field_name(pdf_name, pdf_field_names)
        if not actual:
            logger.warning("PDF has no AcroForm field {!r} (from JSON key {!r})", pdf_name, json_key)
            continue
        updates[actual] = _coerce_pdf_value(raw)

    nt = (narrative_text or "").strip()
    case_narrative = _coerce_pdf_value(_get_nested(data, "narrative")).strip()
    narrative_content = nt or case_narrative

    if narrative_content and not narrative_mapped:
        fb = _narrative_fallback_field(pdf_field_names)
        if fb:
            logger.info("Placing narrative in fallback PDF field {!r}", fb)
            updates[fb] = narrative_content
        else:
            logger.warning(
                "narrative_text/case narrative present but no narrative mapping and no fallback "
                "field (narrative/description/Page 2) found in PDF."
            )

    return updates


def fill_pdf_acroform(
    template_path: Path,
    field_values: dict[str, str],
    output_path: Path,
    *,
    auto_regenerate: bool = True,
) -> None:
    reader = PdfReader(str(template_path))
    writer = PdfWriter()
    writer.append(reader)
    writer.set_need_appearances_writer(True)
    for page in writer.pages:
        writer.update_page_form_field_values(page, field_values, auto_regenerate=auto_regenerate)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as fh:
        writer.write(fh)
    logger.info("Wrote filled PDF to {}", output_path)


def default_output_path(report_type_code: str, outputs_dir: Path | None = None) -> Path:
    outputs_dir = outputs_dir or Path(__file__).resolve().parents[2] / "outputs"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in report_type_code.upper())[:80]
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return outputs_dir / f"output_{safe}_{ts}.pdf"


def run_fill_pipeline(
    pdf_template_url: str,
    pdf_field_mapping: dict[str, Any],
    transaction_json: dict[str, Any],
    narrative_text: str | None,
    report_type_code: str,
    output_path: Path | None = None,
    *,
    sparse_fill: bool = False,
) -> dict[str, Any]:
    template_path = download_template(pdf_template_url)
    reader = PdfReader(str(template_path))
    fields = reader.get_fields() or {}
    pdf_names = set(fields.keys())

    use_sar_new = (
        str(report_type_code).upper() == "SAR" and is_sar_new_mapping(pdf_field_mapping)
    )
    if use_sar_new:
        logger.info("Using SAR new-format mapping (type/label sections)")
        updates = build_sar_field_values(
            pdf_field_mapping, transaction_json, narrative_text, pdf_names, fields
        )
        if not updates and (transaction_json or narrative_text):
            flat_specs, data_key_to_pdf, _ = parse_sar_mapping(pdf_field_mapping)
            data_keys = list(transaction_json.keys())[:15] if transaction_json else []
            logger.warning(
                "SAR new-format produced 0 updates. data_key_to_pdf has %s keys (sample: %s); "
                "flatten/data keys (sample): %s. Check that mapping labels derive to these keys (e.g. '3 - ...' -> '3').",
                len(data_key_to_pdf),
                list(data_key_to_pdf.keys())[:15],
                data_keys,
            )
        else:
            logger.info("SAR new-format built %s field update(s)", len(updates))
        # Checkbox states render better with auto_regenerate=False (pypdf)
        has_checkbox_states = any(
            isinstance(v, str) and v.startswith("/") for v in updates.values()
        )
        auto_regenerate = not has_checkbox_states
    else:
        if str(report_type_code).upper() == "SAR":
            logger.info("Using SAR legacy mapping (flat key -> field)")
        updates = build_field_values(
            pdf_field_mapping, transaction_json, narrative_text, pdf_names, sparse_fill=sparse_fill
        )
        auto_regenerate = True

    out = output_path or default_output_path(report_type_code)
    fill_pdf_acroform(template_path, updates, out, auto_regenerate=auto_regenerate)

    return {
        "status": "success",
        "pdf_path": str(out.resolve()),
        "fields_filled": len(updates),
        "report_type_code": report_type_code,
    }
