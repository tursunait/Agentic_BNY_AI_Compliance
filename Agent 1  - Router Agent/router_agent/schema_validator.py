"""
Extract required field paths from report JSON schema and validate input data.
Uses the same schema shapes as backend (definitions.case, input_payload_schema, required).
"""

from __future__ import annotations

from typing import Any, Dict, List


def _extract_required_paths(
    node: Any,
    prefix: str,
    definitions: Dict[str, Any],
    visited_refs: set,
) -> List[str]:
    if not isinstance(node, dict):
        return []

    out: List[str] = []

    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/definitions/"):
        key = ref.split("/")[-1]
        if key not in visited_refs:
            visited_refs.add(key)
            resolved = definitions.get(key, {})
            out.extend(
                _extract_required_paths(resolved, prefix, definitions, visited_refs)
            )
        return out

    if node.get("type") == "array":
        out.extend(
            _extract_required_paths(
                node.get("items"), prefix, definitions, visited_refs
            )
        )
        return out

    properties = node.get("properties")
    required = node.get("required")
    if isinstance(properties, dict):
        if isinstance(required, list):
            for field in required:
                if not isinstance(field, str):
                    continue
                path = f"{prefix}.{field}" if prefix else field
                out.append(path)
                child = properties.get(field)
                out.extend(
                    _extract_required_paths(
                        child, path, definitions, visited_refs
                    )
                )
        for field, child in properties.items():
            child_prefix = f"{prefix}.{field}" if prefix else field
            out.extend(
                _extract_required_paths(
                    child, child_prefix, definitions, visited_refs
                )
            )
    return out


def get_required_field_paths(schema: Dict[str, Any], report_type: str) -> List[str]:
    """
    Return a sorted list of required field paths for the report type.
    Schema may have definitions.case with required, or input_payload_schema, or required_fields.
    """
    direct = schema.get("required_fields")
    if isinstance(direct, list) and direct:
        return [str(x).strip() for x in direct if str(x).strip()]

    definitions = schema.get("definitions") or {}
    root = schema.get("input_payload_schema") or schema
    paths = _extract_required_paths(root, "", definitions, set())
    deduped = sorted({p for p in paths if p})

    if deduped:
        return deduped

    # Fallbacks if schema has no required
    rt = (report_type or "").upper()
    if rt == "SAR":
        return [
            "case_id", "subject", "subject.name",
            "SuspiciousActivityInformation", "transactions",
        ]
    if rt == "CTR":
        return ["report_type", "case_id", "subject", "subject.name", "institution", "institution.name"]
    return []


def _get_value(data: Dict[str, Any], path: str) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def get_missing_required_fields(
    data: Dict[str, Any],
    required_paths: List[str],
) -> List[str]:
    """
    Given flattened case data (single object, not array), return list of required paths
    that are missing or empty.
    """
    missing: List[str] = []
    for path in required_paths:
        value = _get_value(data, path)
        if _is_empty(value):
            missing.append(path)
    return missing


def normalize_input_to_single_case(payload: Any) -> Dict[str, Any]:
    """
    If payload is a list of cases, return first element as dict.
    Otherwise return payload as dict (or {}).
    """
    if isinstance(payload, list) and len(payload) > 0:
        item = payload[0]
        return item if isinstance(item, dict) else {}
    if isinstance(payload, dict):
        return payload
    return {}
