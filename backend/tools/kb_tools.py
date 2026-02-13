import json
from typing import Any, Dict, Optional

from crewai.tools import tool
from loguru import logger

from backend.knowledge_base.kb_manager import KBManager

_kb: Optional[KBManager] = None


def _safe_dump(data: Any) -> str:
    return json.dumps(data, default=str, ensure_ascii=False)


def _get_kb() -> KBManager:
    global _kb
    if _kb is None:
        _kb = KBManager()
    return _kb


@tool("Search Knowledge Base")
def search_kb_tool(query: str, collection: str = "narratives", filters: Optional[Dict[str, Any]] = None, limit: int = 5) -> str:
    """Search Weaviate collections for related items."""
    try:
        kb = _get_kb()
        if collection == "narratives":
            results = kb.find_similar_narratives(query, activity_type=filters.get("activity_type") if filters else None, top_k=limit)
        elif collection == "regulations":
            results = kb.search_regulations(query, regulation_names=filters.get("regulation_name") if filters else None, top_k=limit)
        elif collection == "definitions":
            results = kb.search_definitions(query, top_k=limit)
        else:
            raise ValueError("Unknown collection %s" % collection)
        return _safe_dump({"results": results})
    except Exception as exc:
        logger.error("Search KB failed: %s", exc)
        return _safe_dump({"error": str(exc)})


@tool("Get Report Schema")
def get_schema_tool(report_type: str) -> str:
    """Fetch the JSON schema for a report type from PostgreSQL."""
    try:
        kb = _get_kb()
        schema = kb.get_schema(report_type)
        return _safe_dump(schema)
    except Exception as exc:
        logger.error("Get schema failed: %s", exc)
        return _safe_dump({"error": str(exc)})


@tool("Get Validation Rules")
def get_validation_rules_tool(report_type: str) -> str:
    """Fetch validation rules for a report type from PostgreSQL."""
    try:
        kb = _get_kb()
        rules = kb.get_validation_rules(report_type)
        return _safe_dump(rules)
    except Exception as exc:
        logger.error("Get validation rules failed: %s", exc)
        return _safe_dump({"error": str(exc)})


@tool("Convert Structured Data to Narrative")
def convert_to_narrative_tool(transaction_data: str) -> str:
    """Convert structured transaction JSON into a narrative summary."""
    try:
        kb = _get_kb()
        payload = json.loads(transaction_data)
        narrative = kb.convert_structured_to_narrative(payload)
        return _safe_dump({"narrative": narrative})
    except Exception as exc:
        logger.error("Convert to narrative failed: %s", exc)
        return _safe_dump({"error": str(exc)})


@tool("Get Field Mappings")
def get_field_mappings_tool(report_type: str) -> str:
    """Fetch field mappings for a report type from PostgreSQL."""
    try:
        kb = _get_kb()
        mappings = kb.get_field_mappings(report_type)
        return _safe_dump(mappings)
    except Exception as exc:
        logger.error("Get field mappings failed: %s", exc)
        return _safe_dump({"error": str(exc)})


@tool("Add to Knowledge Base")
def add_to_kb_tool(collection: str, data: str) -> str:
    """Add a narrative or regulation entry to Weaviate."""
    try:
        kb = _get_kb()
        payload = json.loads(data)
        if collection == "narratives":
            item_id = kb.add_narrative_example(payload)
        elif collection == "regulations":
            item_id = kb.add_regulation(payload)
        else:
            raise ValueError("Unsupported collection %s" % collection)
        return _safe_dump({"status": "ok", "id": item_id})
    except Exception as exc:
        logger.error("Add to KB failed: %s", exc)
        return _safe_dump({"error": str(exc)})
