# Agentic BNY AI Compliance

Containerized multi-agent compliance reporting system.

## What This Project Does

This project accepts transaction data, determines the report type (SAR/CTR/Sanctions), gathers context from a knowledge base, generates a narrative, validates report quality/compliance, and produces a PDF report.

The stack runs as four services:

- `api` (FastAPI + CrewAI orchestration)
- `postgres` (structured/report data + job status)
- `weaviate` (vector search for narratives/regulations/definitions)
- `redis` (short-lived cache)

## Architecture

Services are defined in `docker/docker-compose.yml`.

- `postgres`: durable relational data (`report_schemas`, `validation_rules`, `field_mappings`, `risk_indicators`, `job_status`, `audit_log`)
- `weaviate`: semantic retrieval for similar narratives and regulations
- `redis`: caching for schema/rules/mappings/indicators
- `api`: exposes REST endpoints and runs the multi-agent workflow

## Request Flow (End to End)

1. `POST /api/v1/reports/submit` receives `transaction_data`.
2. API creates a job record in Postgres (`pending`).
3. Background task runs `create_compliance_crew(...)`.
4. Router agent classifies report type and checks KB availability.
5. If KB is missing data, Researcher agent fetches regulatory sources and updates KB.
6. Aggregator + Narrative + Validator agents run sequentially.
7. If approved, Filer agent generates PDF and stores output metadata.
8. Job status/result is updated in Postgres.
9. Client polls status and downloads PDF when ready.

## API Endpoints

Base router prefix: `/api/v1`

- `POST /api/v1/reports/submit`
- `GET /api/v1/reports/{job_id}/status`
- `GET /api/v1/reports/{job_id}/download`
- `GET /api/v1/kb/search`

Health endpoint (outside router prefix):

- `GET /health`

## Agent Roles

- Router: chooses report type and summarizes rationale
- Researcher: finds official regulatory info and updates KB
- Aggregator: maps raw transaction data to report schema and flags missing fields/risk indicators
- Narrative: writes professional compliance narrative
- Validator: checks technical completeness, compliance, and quality
- Filer: produces final PDF report

## Knowledge Base Design

`KBManager` coordinates:

- Postgres for structured compliance metadata
- Weaviate for semantic retrieval
- Redis cache to reduce repeated DB reads
- OpenAI embeddings + fallback LLM narrative generation

Weaviate collections:

- `Narratives`
- `Regulations`
- `Definitions`

## Configuration

Runtime settings are loaded from environment variables (`backend/config/settings.py`).

Key variables:

- `DATABASE_URL`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `WEAVIATE_URL`
- `WEAVIATE_API_KEY`
- `REDIS_URL`
- `OPENAI_API_KEY`
- `GEMINI_API_KEY`

## Local Run

From project root:

```bash
cd docker
docker compose up --build
```

Health check:

```bash
curl http://localhost:8000/health
```

Stop:

```bash
docker compose down
```

Reset volumes (destructive):

```bash
docker compose down -v
```

## Troubleshooting

### 1) `password authentication failed for user "postgres"`

Cause: something is using old Postgres credentials.

Check:

- `DATABASE_URL` in `.env`
- any hardcoded DB URL usage

Current `PostgreSQLClient` resolves DB URL from runtime settings, not hardcoded defaults.

### 2) Weaviate `401 anonymous access not enabled`

Cause: API key auth is enabled in Weaviate but client did not send key.

Fix:

- Ensure `WEAVIATE_API_KEY` is set in `.env`
- Ensure `WEAVIATE_URL=http://weaviate:8080` for in-network container access
- Weaviate client must use API key auth

### 3) CrewAI `Function must have a docstring`

Cause: `@tool` decorated functions without docstrings.

Fix: add docstrings to all tool functions.

### 4) Frequent health-check log noise

`/health` performs real checks for Postgres/Weaviate/Redis. Compose probes every 15s, so failures will repeat on that interval until fixed.

### 5) Warnings about Pydantic/Weaviate versions

Current warnings are non-fatal. They are upgrade hygiene tasks, not runtime blockers.

## Security Notes

- Never commit real secrets.
- `.gitignore` excludes `.env` files.
- Rotate keys if they were ever exposed in logs/chat/screenshots.

## GitHub Push (Quick)

```bash
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/tursunait/Agentic_BNY_AI_Compliance.git
git push -u origin main
```

