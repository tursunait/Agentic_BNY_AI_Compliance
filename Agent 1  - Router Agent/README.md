# Router Agent

Entry-point agent for the BNY AI Compliance multi-agent pipeline. It classifies the compliance report type from the user's input (natural language or JSON), validates that required form fields are present using the Supabase Knowledge Base, and collects any missing information through an interactive chat-style dialogue. The output is a complete case JSON (initial input + filled required fields) ready for the aggregator or downstream pipeline.

---

## Quick start: run the app

All commands below are run from the **repository root** (the folder that contains `router_agent/`).

### 1. Go to repo root

```bash
cd path/to/Agentic_BNY_AI_Compliance
```

### 2. (Optional) Create and activate a virtual environment

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Git Bash / Linux / macOS:**

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
# source .venv/bin/activate     # Linux/macOS
```

### 3. Install dependencies

```bash
pip install -r requirements-router.txt
```

This installs Streamlit, CrewAI, OpenAI, loguru, requests, and other packages used by the router.

### 4. Set environment variables

In the repo root, ensure a `.env` file exists with at least:

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | Used for report-type classification (GPT-4.1 mini). |
| `SUPABASE_URL` | For KB validation | Supabase project URL. When set with `SUPABASE_ANON_KEY`, required fields come from the `required_fields` table. |
| `SUPABASE_ANON_KEY` | For KB validation | Supabase anon/public key. |

If Supabase is not set, the router falls back to the backend KB (Postgres / KBManager).

### 5. Launch the Streamlit app

```bash
streamlit run router_agent/app.py --server.port 8502
```

### 6. Open in browser

Go to: **http://localhost:8502**

### 7. (Optional) Backend API for “Submit to full pipeline”

To send the completed case to the rest of the pipeline, run the backend in a **second terminal** from the repo root:

```bash
uvicorn backend.api.main:app --reload --port 8001
```

The app sidebar uses **API base URL** `http://localhost:8001` by default; you can change it there if needed.

---

## What the Router Agent does

1. **Classify report type** – Uses an LLM (OpenAI GPT-4.1 mini via CrewAI) to determine whether the user needs **SAR**, **CTR**, **Sanctions**, or **BOTH** from free text or from the `report_type` field in JSON.
2. **Check Knowledge Base** – Queries Supabase `report_types` (or backend KB) to confirm the report type exists and has configuration.
3. **Validate input** – Compares the case payload to **required fields** (from Supabase `required_fields` where `is_required = TRUE`, keyed by `input_key`). Only these form-required fields are enforced.
4. **Collect missing info** – If fields are missing, the app shows a **chat-style dialogue**: the agent asks one question at a time using `ask_user_prompt` from `required_fields`, and the user replies in the chat input. This repeats until every required field is filled.
5. **Output complete JSON** – When nothing is missing, the app shows and offers a download of the **complete case JSON** (initial input + all collected required fields) for the aggregator or pipeline.
6. **Submit to pipeline** – Optionally, the user can click “Submit to full pipeline” to POST the complete payload to the backend API.

---

## App flow (Streamlit UI)

- **Text input** – Paste a free-text case description (and optional subject name), then click **Classify & validate**.
- **JSON input** – Paste case JSON (strict JSON or Python-style dict with single quotes, `True`/`False`/`None`), then click **Classify & validate**.
- **Router result** – Report type, KB status, confidence, and reasoning are shown. If required fields are missing, you see a short message and an expander with the list of missing paths.
- **Chat (missing information)** – One question at a time from the agent (using `ask_user_prompt`). Reply in the chat input and press Enter; the agent asks the next question until all required fields are filled.
- **Complete case JSON** – After validation passes, the full merged JSON is displayed and a **Download complete JSON** button is available.
- **Submit to full pipeline** – Sends the complete payload to the backend (e.g. `POST .../api/v1/reports/submit`). Requires the backend to be running.

---

## Repository layout (`router_agent/`)

| File | Purpose |
|------|--------|
| `app.py` | Streamlit UI: text/JSON tabs, classify & validate, chat for missing fields, complete JSON display/download, submit to backend. |
| `run.py` | Orchestration: classify → check KB → get required paths → validate → return `RouterResult` (report_type, missing_fields, missing_field_prompts, message, etc.). |
| `agent.py` | CrewAI agent and task for report-type classification (SAR/CTR/Sanctions/BOTH) using the configured LLM. |
| `kb_client.py` | Knowledge Base client: when Supabase is enabled, uses `report_types` and `required_fields`; otherwise uses backend `KBManager`. Exposes `get_required_field_paths` and `get_required_fields_with_prompts`. |
| `supabase_rest.py` | Supabase REST client for `report_types` and `required_fields` (filter by `report_type_code`, `is_required=true`). Fetches `input_key`, `ask_user_prompt`, `field_label`. |
| `schema_validator.py` | Validates payload against required paths; returns missing paths. Used when Supabase is not the source. |
| `config.py` | `ROUTER_LLM_MODEL`, `SUPPORTED_REPORT_TYPES`, `API_BASE_URL`. |

---

## Supabase tables (when used)

- **`report_types`** – One row per report type (`report_type_code`, e.g. `SAR`). Used to check that the type exists and optionally to get `json_schema`.
- **`required_fields`** – Defines which fields are required for the form and how to ask the user:
  - `report_type_code` – e.g. `SAR`
  - `input_key` – Dot path in the case JSON (e.g. `subject.first_name`, `filing_institution.address`)
  - `is_required` – Only rows with `is_required = TRUE` are used for validation and prompts.
  - `ask_user_prompt` – Question shown in the chat (e.g. “What is the date range of the suspicious activity?”).
  - `field_label` – Fallback label if `ask_user_prompt` is empty.

The router **only** considers `required_fields` rows with `is_required = TRUE` as the source of truth for “missing” fields and for the chat prompts.

---

## Using the router from Python

```python
from router_agent import run_router, RouterResult

# Free-text input
result = run_router("I need to file a SAR for a suspicious wire transfer.")
print(result.report_type)           # e.g. SAR
print(result.kb_status)             # EXISTS | MISSING
print(result.missing_fields)        # e.g. ["subject.first_name", "amount"]
print(result.missing_field_prompts) # [{"input_key": "...", "ask_user_prompt": "...", "field_label": "..."}]
print(result.message)

# Structured JSON input (report_type can be taken from payload)
result = run_router({"report_type": "SAR", "subject": {"name": "John"}, ...})
```

---

## Configuration

- **LLM** – Set in `config.py`: `ROUTER_LLM_MODEL = "gpt-4.1-mini"` (used by CrewAI in `agent.py`).
- **Backend URL** – Default `http://localhost:8001`; overridable via env `COMPLIANCE_API_BASE_URL` or the sidebar in the app.

---

## Troubleshooting

- **`ModuleNotFoundError: No module named 'loguru'`** (or similar) – Install deps: `pip install -r requirements-router.txt` in the same environment you use to run Streamlit.
- **“No required fields” / wrong missing list** – Ensure `SUPABASE_URL` and `SUPABASE_ANON_KEY` are set and that `required_fields` has rows with `is_required = TRUE` and correct `input_key` values for your case payload paths.
- **Invalid JSON** – The app accepts both strict JSON (double quotes) and Python-style dicts (single quotes, `True`/`False`/`None`). If you still see an error, check for trailing commas or non-literal values.
