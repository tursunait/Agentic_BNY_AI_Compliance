import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from redis import Redis
from sqlalchemy import text

from backend.api.routes import router
from backend.config.settings import settings
from backend.knowledge_base.supabase_client import SupabaseClient
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


def _check_database_connection() -> bool:
    try:
        db = SupabaseClient()
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _check_weaviate_connection() -> bool:
    try:
        client = WeaviateClient(settings.WEAVIATE_URL, settings.WEAVIATE_API_KEY)
        return client.client.is_ready() if hasattr(client.client, "is_ready") else True
    except Exception:
        return False


def _check_redis_connection() -> bool:
    try:
        redis = Redis.from_url(settings.REDIS_URL)
        return redis.ping()
    except Exception:
        return False


@app.get("/health")
async def health_check() -> dict:
    loop = asyncio.get_event_loop()
    db_ok, weaviate_ok, redis_ok = await asyncio.gather(
        loop.run_in_executor(None, _check_database_connection),
        loop.run_in_executor(None, _check_weaviate_connection),
        loop.run_in_executor(None, _check_redis_connection),
    )
    return {
        "status": "healthy",
        "services": {
            "database": db_ok,
            "weaviate": weaviate_ok,
            "redis": redis_ok,
        },
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
