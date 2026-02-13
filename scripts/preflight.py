"""Validate local environment and service connectivity before running the API."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Tuple

import requests
from redis import Redis
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config.settings import settings


def _check_postgres() -> Tuple[bool, str]:
    try:
        engine = create_engine(settings.DATABASE_URL, future=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "connected"
    except Exception as exc:
        return False, str(exc)


def _check_redis() -> Tuple[bool, str]:
    try:
        client = Redis.from_url(settings.REDIS_URL)
        return bool(client.ping()), "connected"
    except Exception as exc:
        return False, str(exc)


def _check_weaviate() -> Tuple[bool, str]:
    try:
        url = settings.WEAVIATE_URL.rstrip("/") + "/v1/.well-known/ready"
        headers = {}
        if settings.WEAVIATE_API_KEY:
            headers["Authorization"] = f"Bearer {settings.WEAVIATE_API_KEY}"
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return True, "ready"
        return False, f"http {response.status_code}"
    except Exception as exc:
        return False, str(exc)


def _check_openai_key() -> Tuple[bool, str]:
    if settings.OPENAI_API_KEY and settings.OPENAI_API_KEY.startswith("sk-"):
        return True, "set"
    if settings.OPENAI_API_KEY:
        return True, "set (format not validated)"
    return False, "missing"


def main() -> int:
    checks = [
        ("Python", True, sys.executable),
        ("PostgreSQL",) + _check_postgres(),
        ("Redis",) + _check_redis(),
        ("Weaviate",) + _check_weaviate(),
        ("OPENAI_API_KEY",) + _check_openai_key(),
    ]

    print("Preflight checks:")
    has_failure = False
    for name, ok, detail in checks:
        status = "OK" if ok else "FAIL"
        print(f"- {name}: {status} ({detail})")
        if not ok and name in {"PostgreSQL", "Redis", "Weaviate", "OPENAI_API_KEY"}:
            has_failure = True

    if has_failure:
        print("\nOne or more required checks failed.")
        return 1

    print("\nAll required checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
