# Agentic BNY AI Compliance

Multi-agent compliance reporting system.

## What This Project Does

This project accepts transaction data, determines the report type (SAR/CTR/Sanctions), gathers context from a knowledge base, generates a narrative, validates report quality/compliance, and produces a PDF report.

## Architecture

Core components:

- API: FastAPI + CrewAI orchestration
- PostgreSQL: durable relational data (`report_schemas`, `validation_rules`, `field_mappings`, `risk_indicators`, `job_status`, `audit_log`)
- Weaviate: semantic retrieval for similar narratives and regulations
- Redis: caching for schema/rules/mappings/indicators

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

1. Create `.env` from template:

```bash
cp .env.example .env
```

2. Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. If you use Conda, deactivate it first so scripts use `.venv` packages:

```bash
conda deactivate
source .venv/bin/activate
which python
```

4. Configure environment variables in `.env`.

- For Weaviate Cloud, use a full HTTPS URL and API key:
  - `WEAVIATE_URL=https://<your-cluster>.weaviate.cloud`
  - `WEAVIATE_API_KEY=<your-weaviate-api-key>`
- For local Weaviate:
  - `WEAVIATE_URL=http://localhost:8080`
  - `WEAVIATE_API_KEY=` (empty for anonymous local mode)

5. Ensure PostgreSQL, Redis, and Weaviate are reachable from `.env`.

6. Run preflight checks (recommended for teammates):

```bash
python scripts/preflight.py
```

7. Initialize and seed the knowledge base:

```bash
python scripts/init_weaviate.py
python scripts/seed_kb.py
```

8. Start the API from project root (use any free port):

```bash
uvicorn backend.api.main:app --host 0.0.0.0 --port 8001 --reload
```

9. Health check:

```bash
curl http://localhost:8001/health
```

Expected response:

```json
{"status":"healthy","services":{"postgres":true,"weaviate":true,"redis":true}}
```

10. Stop with `Ctrl+C`.

## Team Onboarding Checklist

Use this exact sequence on a new machine:

```bash
git clone <repo-url>
cd Agentic_BNY_AI_Compliance
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/preflight.py
python scripts/init_weaviate.py
python scripts/seed_kb.py
uvicorn backend.api.main:app --host 0.0.0.0 --port 8001 --reload
```

If `python scripts/preflight.py` fails, fix those service/env issues first before running seed/API.

## External Services

The API depends on:

- PostgreSQL (for report schemas, rules, jobs, audit data)
- Weaviate (for vector search and retrieval)
- Redis (for caching)

The app can start even if some services are down, but feature behavior depends on them:

- Postgres down: report job/state operations fail
- Redis down: cache falls back to direct datastore reads
- Weaviate down: semantic KB operations fail (search/add/regulatory retrieval)

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
- Ensure `WEAVIATE_URL` points to your reachable Weaviate endpoint
- Weaviate client must use API key auth

### 3) Weaviate `MissingSchema: Invalid URL ... No scheme supplied`

Cause: `WEAVIATE_URL` is missing `http://` or `https://`.

Fix:

- Cloud: `WEAVIATE_URL=https://<cluster>.weaviate.cloud`
- Local: `WEAVIATE_URL=http://localhost:8080`

### 4) `ModuleNotFoundError: No module named 'pkg_resources'`

Cause: CrewAI dependency path requires `pkg_resources`; newer `setuptools` removed it.

Fix:

- Keep `setuptools<81` (already pinned in `requirements.txt`)
- Reinstall in venv: `python -m pip install --force-reinstall "setuptools<81"`

### 5) `Address already in use` when starting Uvicorn

Cause: selected port is already bound by another process.

Fix:

- Use a free port (for example `8002`)
- Or stop the existing listener:
  - `kill -9 $(lsof -ti tcp:8001 -sTCP:LISTEN) 2>/dev/null || true`

### 6) CrewAI `Function must have a docstring`

Cause: `@tool` decorated functions without docstrings.

Fix: add docstrings to all tool functions.

### 7) Frequent health-check log noise

`/health` performs real checks for Postgres/Weaviate/Redis. If any upstream dependency is misconfigured, failures will repeat on each health call until fixed.

### 8) Warnings about Pydantic/Weaviate versions

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

Example runs: 


curl http://localhost:8001/health    


```bash
source .venv/bin/activate
python scripts/test_pdf_filer.py --input data/CASE-2023-687870__CAT-29-33-35.json --report-type SAR
```

```bash
source .venv/bin/activate
python scripts/test_pdf_filer.py --input data/CASE-2024-170507__CAT-29-33-35.json --report-type SAR
```

```bash
source .venv/bin/activate
python scripts/test_pdf_filer.py --input data/CASE-2025-456266__CAT-29-31-33-35.json --report-type SAR
```

```bash
source .venv/bin/activate
python scripts/test_pdf_filer.py --input data/ctr_test_case.json --report-type CTR
```

ctr_report.pdf