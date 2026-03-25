from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

from loguru import logger
from redis import Redis
from redis.exceptions import RedisError
import requests

from backend.config.settings import settings
from backend.knowledge_base.supabase_client import SupabaseClient
from backend.knowledge_base.weaviate_client import WeaviateClient
from backend.utils.llm_client import OpenAIClient


class KBManager:
    def __init__(self):
        self.database = SupabaseClient(database_url=settings.get_database_url())
        self.weaviate = WeaviateClient(settings.WEAVIATE_URL, settings.WEAVIATE_API_KEY)
        self.redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.openai = OpenAIClient()
        logger.debug("KBManager initialized with services")

    # ===== DATABASE HELPERS =====

    def _get_cached(self, key: str) -> Optional[Dict[str, Any]]:
        try:
            raw = self.redis.get(key)
            if not raw:
                return None
            return json.loads(raw)
        except RedisError as exc:
            logger.warning("Redis cache read failed: %s", exc)
            return None

    def _set_cached(self, key: str, value: Dict[str, Any], ttl: int = 300) -> None:
        try:
            self.redis.set(key, json.dumps(value), ex=ttl)
        except RedisError as exc:
            logger.warning("Redis cache write failed: %s", exc)

    def _supabase_rest_enabled(self) -> bool:
        return bool(settings.get_supabase_rest_url() and settings.SUPABASE_ANON_KEY)

    def _supabase_headers(self) -> Dict[str, str]:
        return {
            "apikey": settings.SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {settings.SUPABASE_ANON_KEY}",
        }

    @staticmethod
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

    def _supabase_select(self, table: str, params: Dict[str, str]) -> List[Dict[str, Any]]:
        if not self._supabase_rest_enabled():
            return []
        url = f"{settings.get_supabase_rest_url().rstrip('/')}/rest/v1/{table}"
        try:
            response = requests.get(url, headers=self._supabase_headers(), params=params, timeout=15)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list):
                return payload
            return []
        except Exception as exc:
            logger.error("Supabase REST read failed for table {}: {}", table, exc)
            raise

    def _fetch_report_type_row(self, report_type: str) -> Optional[Dict[str, Any]]:
        code = report_type.upper()
        attempts = [
            {"select": "*", "report_type": f"eq.{code}", "limit": "1"},
            {"select": "*", "report_type_code": f"eq.{code}", "limit": "1"},
            {"select": "*", "limit": "1"},
        ]
        for params in attempts:
            try:
                rows = self._supabase_select("report_types", params)
            except Exception:
                continue
            if rows:
                return rows[0]
        return None

    @staticmethod
    def _normalize_rules(raw_rules: Any) -> List[Dict[str, Any]]:
        value = KBManager._coerce_json(raw_rules)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = value.get("rules")
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
            return [value]
        return []

    @staticmethod
    def _normalize_field_mappings(raw_mappings: Any) -> List[Dict[str, Any]]:
        value = KBManager._coerce_json(raw_mappings)
        if isinstance(value, list):
            normalized = []
            for item in value:
                if isinstance(item, dict):
                    normalized.append(
                        {
                            "source_field": item.get("source_field"),
                            "target_field": item.get("target_field"),
                            "transformation": item.get("transformation"),
                        }
                    )
            return [item for item in normalized if item.get("source_field") or item.get("target_field")]
        if isinstance(value, dict):
            # Supports shorthand dict format: {"source.path": "target_field_id"}
            normalized = []
            for source_field, target in value.items():
                if isinstance(target, dict):
                    normalized.append(
                        {
                            "source_field": source_field,
                            "target_field": target.get("target_field"),
                            "transformation": target.get("transformation"),
                        }
                    )
                else:
                    normalized.append(
                        {
                            "source_field": source_field,
                            "target_field": target,
                            "transformation": None,
                        }
                    )
            return [item for item in normalized if item.get("source_field") and item.get("target_field")]
        return []

    def _fetch_narrative_examples(self, query: str, limit: int) -> List[Dict[str, Any]]:
        params: Dict[str, str] = {
            "select": "summary,narrative_text,effectiveness_notes,example_order",
            "order": "example_order.asc",
            "limit": str(limit),
        }
        query_text = " ".join((query or "").split()).replace(",", " ").replace("(", " ").replace(")", " ")
        if query_text:
            pattern = f"*{query_text}*"
            params["or"] = (
                f"(summary.ilike.{pattern},"
                f"narrative_text.ilike.{pattern},"
                f"effectiveness_notes.ilike.{pattern})"
            )
        return self._supabase_select("narrative_examples", params)

    def get_schema(self, report_type: str) -> Dict[str, Any]:
        cache_key = f"schema:{report_type}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        schema: Optional[Dict[str, Any]] = None
        if self._supabase_rest_enabled():
            row = self._fetch_report_type_row(report_type)
            if row:
                parsed = self._coerce_json(row.get("json_schema"))
                if isinstance(parsed, dict):
                    schema = parsed
                else:
                    schema = {
                        "report_type": report_type,
                        "template_file": row.get("pdf_template_path"),
                        "narrative_instructions": row.get("narrative_instructions"),
                    }
            else:
                logger.warning("report_types REST lookup failed for {} — falling back to local DB", report_type)
                schema = self.database.get_schema(report_type)
        else:
            schema = self.database.get_schema(report_type)

        if not schema:
            raise ValueError("Schema not found in database for %s" % report_type)
        self._set_cached(cache_key, schema)
        return schema

    def get_validation_rules(self, report_type: str) -> List[Dict[str, Any]]:
        cache_key = f"validation_rules:{report_type}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached.get("rules", [])

        rules: List[Dict[str, Any]] = []
        if self._supabase_rest_enabled():
            row = self._fetch_report_type_row(report_type)
            if row:
                rules = self._normalize_rules(row.get("validation_rules"))
            else:
                logger.warning("report_types REST lookup failed for {} — falling back to local DB", report_type)
                rules = self.database.get_validation_rules(report_type)
        else:
            rules = self.database.get_validation_rules(report_type)

        self._set_cached(cache_key, {"rules": rules})
        return rules

    def get_field_mappings(self, report_type: str) -> List[Dict[str, Any]]:
        cache_key = f"field_mappings:{report_type}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached.get("mappings", [])

        mappings: List[Dict[str, Any]] = []
        if self._supabase_rest_enabled():
            row = self._fetch_report_type_row(report_type)
            if row:
                mappings = self._normalize_field_mappings(row.get("pdf_field_mapping"))
            else:
                logger.warning("report_types REST lookup failed for {} — falling back to local DB", report_type)
                mappings = self.database.get_field_mappings(report_type)
        else:
            mappings = self.database.get_field_mappings(report_type)

        self._set_cached(cache_key, {"mappings": mappings})
        return mappings

    def get_risk_indicators(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        cache_key = f"risk_indicators:{category or 'all'}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached.get("indicators", [])
        indicators = self.database.get_risk_indicators(category)
        self._set_cached(cache_key, {"indicators": indicators})
        return indicators

    # ===== VECTOR SEARCH HELPERS =====

    def find_similar_narratives(
        self,
        query: str,
        activity_type: Optional[str] = None,
        min_quality: float = 8.0,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        if self._supabase_rest_enabled():
            rows = self._fetch_narrative_examples(query, top_k)
            results = [
                {
                    "summary": row.get("summary", ""),
                    "text": row.get("narrative_text", ""),
                    "effectiveness_notes": row.get("effectiveness_notes", ""),
                    "example_order": row.get("example_order"),
                    "quality_score": 10.0,
                    "report_type": "SAR",
                    "activity_type": activity_type or "",
                }
                for row in rows
                if isinstance(row, dict)
            ]
            return results[:top_k]

        filters: Dict[str, Any] = {}
        if activity_type:
            filters["activity_type"] = activity_type
        raw_results = self.weaviate.search_narratives(query, filters=filters or None, limit=top_k)
        filtered = [item for item in raw_results if item.get("quality_score", 0) >= min_quality]
        if not filtered:
            filtered = raw_results[:top_k]
        return filtered

    def search_regulations(
        self,
        query: str,
        regulation_names: Optional[List[str]] = None,
        top_k: int = 10,
    ) -> List[Dict[str, Any]]:
        filters = {"regulation_name": regulation_names[0]} if regulation_names else None
        return self.weaviate.search_regulations(query, limit=top_k)

    def search_definitions(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        return self.weaviate.search_definitions(query, limit=top_k)

    # ===== HYBRID OPERATIONS =====

    def _template_structuring(self, transaction_data: Dict[str, Any]) -> Optional[str]:
        deposits = [tx for tx in transaction_data.get("transactions", []) if tx.get("type") == "cash_deposit"]
        if not deposits:
            return None
        total = sum(tx.get("amount", 0) for tx in deposits)
        if len(deposits) >= 3 and total > 20000:
            return (
                f"Customer made {len(deposits)} cash deposits over {total:.2f} USD across multiple branches,"
                " consistently below the $10,000 CTR threshold, suggesting structuring."
            )
        return None

    def _template_wire(self, transaction_data: Dict[str, Any]) -> Optional[str]:
        wires = [tx for tx in transaction_data.get("transactions", []) if tx.get("type") == "wire_transfer"]
        if not wires:
            return None
        countries = {tx.get("destination_country") for tx in wires}
        return (
            f"Multiple wire transfers were sent to {', '.join(sorted(filter(None, countries)))} totaling "
            f"{sum(tx.get('amount', 0) for tx in wires):.2f} USD, which may trigger scrutiny."
        )

    def _llm_narrative(self, transaction_data: Dict[str, Any]) -> str:
        prompt = (
            f"Summarize the following transaction data in a brief compliance narrative: {json.dumps(transaction_data)}"
        )
        try:
            response = self.openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a compliance writer."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
            )
            return response.choices[0].message.content.strip()
        except Exception as exc:
            logger.error("Fallback narrative LLM failed: %s", exc)
            return "Structured transaction patterns detected; review manually."

    def convert_structured_to_narrative(self, transaction_data: Dict[str, Any]) -> str:
        narrative = self._template_structuring(transaction_data)
        if narrative:
            return narrative
        narrative = self._template_wire(transaction_data)
        if narrative:
            return narrative
        return self._llm_narrative(transaction_data)

    def get_complete_context(
        self,
        report_type: str,
        transaction_data: Dict[str, Any],
        case_description: str,
    ) -> Dict[str, Any]:
        narrative = self.convert_structured_to_narrative(transaction_data)
        return {
            "schema": self.get_schema(report_type),
            "validation_rules": self.get_validation_rules(report_type),
            "field_mappings": self.get_field_mappings(report_type),
            "similar_narratives": self.find_similar_narratives(narrative, top_k=5),
            "regulations": self.search_regulations(case_description, top_k=5),
            "risk_indicators": self.get_risk_indicators(),
        }

    # ===== KB UPDATES =====

    def add_narrative_example(self, narrative_data: Dict[str, Any]) -> str:
        return self.weaviate.add_narrative(narrative_data)

    def add_regulation(self, regulation_data: Dict[str, Any]) -> str:
        return self.weaviate.add_regulation(regulation_data)

    def update_schema(self, report_type: str, schema: Dict[str, Any]) -> None:
        # For simplicity, create a new version and mark existing inactive
        self.database.add_schema(report_type, schema.get("version", "1.0"), schema, schema.get("effective_date", "2025-01-01"))

    # ===== UTILITY =====

    def log_audit(self, session_id: str, action: str, details: Dict[str, Any]) -> str:
        return str(
            self.database.log_audit(
                session_id=uuid.UUID(session_id),
                action=action,
                agent_name=details.get("agent", "unknown"),
                entity_type=details.get("entity_type", "case"),
                entity_id=details.get("entity_id"),
                details=details,
            )
        )

    def get_validation_rules_raw(self, report_type: str) -> List[Dict[str, Any]]:
        # alias for clarity
        return self.get_validation_rules(report_type)
