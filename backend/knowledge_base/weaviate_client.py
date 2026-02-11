from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import weaviate
from loguru import logger
from weaviate.gql.filter import Where

from backend.config.settings import settings
from backend.utils.llm_client import OpenAIClient

class _CollectionConfig:
    def __init__(self, name: str, properties: List[Dict[str, Any]]):
        self.name = name
        self.vectorizer = "none"
        self.vector_dim = 3072
        self.distance = "cosine"
        self.properties = properties


NARRATIVES_CONFIG = _CollectionConfig(
    "Narratives",
    properties=[
        {"name": "text", "dataType": ["text"]},
        {"name": "summary", "dataType": ["text"]},
        {"name": "activity_type", "dataType": ["string"]},
        {"name": "report_type", "dataType": ["string"]},
        {"name": "quality_score", "dataType": ["number"]},
        {"name": "word_count", "dataType": ["number"]},
        {"name": "transaction_count", "dataType": ["number"]},
        {"name": "total_amount", "dataType": ["number"]},
        {"name": "date_added", "dataType": ["date"]},
    ],
)

REGULATIONS_CONFIG = _CollectionConfig(
    "Regulations",
    properties=[
        {"name": "text", "dataType": ["text"]},
        {"name": "regulation_name", "dataType": ["string"]},
        {"name": "section", "dataType": ["string"]},
        {"name": "effective_date", "dataType": ["date"]},
        {"name": "source_url", "dataType": ["string"]},
    ],
)

DEFINITIONS_CONFIG = _CollectionConfig(
    "Definitions",
    properties=[
        {"name": "term", "dataType": ["string"]},
        {"name": "definition", "dataType": ["text"]},
        {"name": "context", "dataType": ["string"]},
        {"name": "source", "dataType": ["string"]},
    ],
)


class WeaviateClient:
    def __init__(self, url: str, api_key: str):
        self.url = url
        self.api_key = api_key
        self.openai = OpenAIClient()
        self.client = weaviate.Client(
            url=self.url,
            additional_headers={"X-API-KEY": self.api_key},
            timeout_config=(5, 30),
        )
        logger.debug("Weaviate client initialized at %s", self.url)

    def create_schema(self) -> None:
        configs = [NARRATIVES_CONFIG, REGULATIONS_CONFIG, DEFINITIONS_CONFIG]
        for config in configs:
            try:
                if self.client.schema.contains(config.name):
                    logger.debug("Collection %s already exists", config.name)
                    continue

                schema = {
                    "class": config.name,
                    "vectorizer": config.vectorizer,
                    "properties": config.properties,
                    "vectorIndexConfig": {
                        "distance": config.distance,
                        "efConstruction": 128,
                        "maxConnections": 64,
                    },
                    "vectorDimension": config.vector_dim,
                }
                self.client.schema.create_collection(schema)
                logger.info("Created collection %s", config.name)
            except Exception as exc:
                logger.error("Failed to create collection %s: %s", config.name, exc)
                raise

    @staticmethod
    def _ensure_openai_key() -> None:
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OpenAI API key not configured")

    def embed_text(self, text: str) -> List[float]:
        if not text:
            raise ValueError("Text for embedding cannot be empty")
        self._ensure_openai_key()
        for attempt in range(3):
            try:
                response = self.openai.embeddings.create(model="text-embedding-3-large", input=text)
                vector = response.data[0].embedding
                if len(vector) != 3072:
                    logger.warning("Embedding length %s differs from expectation", len(vector))
                return vector
            except Exception as exc:
                logger.warning("Embedding attempt %s failed: %s", attempt + 1, exc)
                time.sleep((attempt + 1) * 0.5)
        raise RuntimeError("Failed to generate embedding after retries")

    @staticmethod
    def _build_where_clause(filters: Optional[Dict[str, Any]]) -> Optional[Where]:
        if not filters:
            return None
        clauses: List[Where] = []
        for key, value in filters.items():
            clauses.append(
                Where(
                    path=[key],
                    operator="Equal",
                    value_text=str(value),
                )
            )
        if not clauses:
            return None
        return clauses[0] if len(clauses) == 1 else Where(operator="And", operands=clauses)

    def add_narrative(self, narrative_data: Dict[str, Any]) -> str:
        vector = self.embed_text(narrative_data.get("text", ""))
        payload = {k: v for k, v in narrative_data.items() if k != "id"}
        object_id = self.client.data_object.create(
            data_object=payload,
            class_name=NARRATIVES_CONFIG.name,
            vector=vector,
        )
        logger.info("Narrative created: %s", object_id)
        return object_id

    def add_regulation(self, regulation_data: Dict[str, Any]) -> str:
        vector = self.embed_text(regulation_data.get("text", ""))
        payload = {k: v for k, v in regulation_data.items() if k != "id"}
        object_id = self.client.data_object.create(
            data_object=payload,
            class_name=REGULATIONS_CONFIG.name,
            vector=vector,
        )
        logger.info("Regulation chunk created: %s", object_id)
        return object_id

    def search_narratives(self, query: str, filters: Optional[Dict[str, Any]] = None, limit: int = 5) -> List[Dict[str, Any]]:
        vector = self.embed_text(query)
        builder = (
            self.client.query.get(
                NARRATIVES_CONFIG.name,
                [
                    "text",
                    "summary",
                    "activity_type",
                    "report_type",
                    "quality_score",
                    "transaction_count",
                    "total_amount",
                ],
            )
            .with_near_vector({"vector": vector})
            .with_limit(limit)
            .with_additional("distance")
        )
        where_clause = self._build_where_clause(filters)
        if where_clause:
            builder = builder.with_where(where_clause)
        response = builder.do()
        return response.get("data", {}).get("Get", {}).get(NARRATIVES_CONFIG.name, [])

    def search_regulations(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        vector = self.embed_text(query)
        builder = (
            self.client.query.get(
                REGULATIONS_CONFIG.name,
                ["text", "regulation_name", "section", "effective_date", "source_url"],
            )
            .with_near_vector({"vector": vector})
            .with_limit(limit)
            .with_additional("distance")
        )
        response = builder.do()
        return response.get("data", {}).get("Get", {}).get(REGULATIONS_CONFIG.name, [])

    def search_definitions(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        vector = self.embed_text(query)
        builder = (
            self.client.query.get(
                DEFINITIONS_CONFIG.name,
                ["term", "definition", "context", "source"],
            )
            .with_near_vector({"vector": vector})
            .with_limit(limit)
            .with_additional("distance")
        )
        response = builder.do()
        return response.get("data", {}).get("Get", {}).get(DEFINITIONS_CONFIG.name, [])

    def hybrid_search(self, query: str, filters: Optional[Dict[str, Any]] = None, alpha: float = 0.5, limit: int = 10) -> List[Dict[str, Any]]:
        builder = (
            self.client.query.get(
                NARRATIVES_CONFIG.name,
                ["text", "summary", "activity_type", "report_type", "quality_score"],
            )
            .with_hybrid({"query": query, "alpha": alpha})
            .with_limit(limit)
            .with_additional("distance")
        )
        where_clause = self._build_where_clause(filters)
        if where_clause:
            builder = builder.with_where(where_clause)
        response = builder.do()
        return response.get("data", {}).get("Get", {}).get(NARRATIVES_CONFIG.name, [])

    def delete_collection(self, collection_name: str) -> None:
        self.client.schema.delete_collection(collection_name)
        logger.info("Deleted collection %s", collection_name)
