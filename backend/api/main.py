from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from redis import Redis
from sqlalchemy import text

from backend.api.routes import router
from backend.config.settings import settings
from backend.knowledge_base.postgres_client import PostgreSQLClient
from backend.knowledge_base.weaviate_client import WeaviateClient

app = FastAPI(
    title="AI Compliance Reporting System",
    description="Multi-agent system for automated compliance report generation",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")


def check_postgres_connection() -> bool:
    try:
        db = PostgreSQLClient()
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def check_weaviate_connection() -> bool:
    try:
        client = WeaviateClient(settings.WEAVIATE_URL, settings.WEAVIATE_API_KEY)
        return client.client.is_ready() if hasattr(client.client, "is_ready") else True
    except Exception:
        return False


def check_redis_connection() -> bool:
    try:
        redis = Redis.from_url(settings.REDIS_URL)
        return redis.ping()
    except Exception:
        return False


@app.get("/health")
async def health_check() -> dict:
    return {
        "status": "healthy",
        "services": {
            "postgres": check_postgres_connection(),
            "weaviate": check_weaviate_connection(),
            "redis": check_redis_connection(),
        },
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
