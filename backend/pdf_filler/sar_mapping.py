"""
Parse the new SAR pdf_field_mapping format from Supabase.

Format: nested sections (e.g. part_I_type_of_filing) with PDF field names as keys
and { "type": "checkbox"|"text", "label": "1a - Description" } as values.
Optional: "data_key" in value to link to flatten keys explicitly.
"""

from __future__ import annotations

import re
from typing import Any


def _label_to_data_key(label: str | None) -> str | None:
    """Derive data key from label: part before ' - ' (e.g. '1a - Initial' -> '1a'), or first token like 3/1a."""
    if not label or not isinstance(label, str):
        return None
    label = label.strip()
    idx = label.find(" - ")
    if idx >= 0:
        part = label[:idx].strip()
        if part:
            return part
    # First token: digits + optional letter (e.g. "1a", "3") or "Item 3" -> "3"
    m = re.match(r"^\s*(\d+[a-z]?)\b", label)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d+[a-z]?)\b", label)
    if m:
        return m.group(1)
    if re.match(r"^[a-zA-Z]\d*$", label[:3]):
        return label.split()[0] if label.split() else None
    return None


def parse_sar_mapping(nested_mapping: dict[str, Any]) -> tuple[dict[str, dict], dict[str, str], set[str]]:
    """
    Parse new-format SAR mapping from DB.

    Returns:
        flat_specs: pdf_field_name -> { "type": "checkbox"|"text", "label": "..." }
        data_key_to_pdf: data_key (e.g. "3", "1a") -> pdf_field_name (for filling from flatten output)
        checkbox_field_names: set of pdf_field_name that are checkboxes
    """
    flat_specs: dict[str, dict] = {}
    data_key_to_pdf: dict[str, str] = {}
    checkbox_field_names: set[str] = set()

    if not isinstance(nested_mapping, dict):
        return flat_specs, data_key_to_pdf, checkbox_field_names

    for _section_name, section in nested_mapping.items():
        if not isinstance(section, dict):
            continue
        for pdf_field_name, spec in section.items():
            if not isinstance(spec, dict):
                continue
            spec = {k: v for k, v in spec.items() if v is not None}
            field_type = (spec.get("type") or "text").strip().lower()
            label = spec.get("label") or ""
            data_key = spec.get("data_key")
            if data_key is None or (isinstance(data_key, str) and not data_key.strip()):
                data_key = _label_to_data_key(label)
            if isinstance(data_key, str):
                data_key = data_key.strip()
            else:
                data_key = str(data_key).strip() if data_key is not None else None

            flat_specs[pdf_field_name] = {"type": field_type, "label": label}
            if field_type == "checkbox":
                checkbox_field_names.add(pdf_field_name)
            if data_key:
                # Prefer text over checkbox when same data_key (e.g. "3" -> text field for name)
                existing = data_key_to_pdf.get(data_key)
                if existing and flat_specs.get(existing, {}).get("type") == "text":
                    pass  # keep existing text
                else:
                    data_key_to_pdf[data_key] = pdf_field_name

    return flat_specs, data_key_to_pdf, checkbox_field_names


def is_sar_new_mapping(pdf_field_mapping: dict[str, Any]) -> bool:
    """True if mapping is the new nested format (sections with type/label per field)."""
    if not isinstance(pdf_field_mapping, dict) or not pdf_field_mapping:
        return False
    for _section, section in pdf_field_mapping.items():
        if not isinstance(section, dict):
            continue
        for _name, spec in section.items():
            if isinstance(spec, dict) and "type" in spec:
                return True
    return False
