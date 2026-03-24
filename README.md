<p align="center">
  <img src="img/logo.png" alt="Compliance AI" height="100"/>
</p>

<h1 align="center">Agentic AI Compliance System</h1>

<p align="center">
  End-to-end automated generation of FinCEN SAR, CTR, and OFAC Rejection Reports<br/>
  powered by a multi-agent AI pipeline built on CrewAI and GPT-4o
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-blue?logo=python&logoColor=white"/>
  <img src="https://img.shields.io/badge/CrewAI-Multi--Agent-orange"/>
  <img src="https://img.shields.io/badge/OpenAI-GPT--4o-412991?logo=openai&logoColor=white"/>
  <img src="https://img.shields.io/badge/FastAPI-REST%20API-009688?logo=fastapi&logoColor=white"/>
  <img src="https://img.shields.io/badge/Streamlit-Dashboard-FF4B4B?logo=streamlit&logoColor=white"/>
  <img src="https://img.shields.io/badge/Supabase-PostgreSQL-3ECF8E?logo=supabase&logoColor=white"/>
  <img src="https://img.shields.io/badge/Weaviate-Vector%20DB-00C8AA"/>
</p>

---

## Table of Contents

1. [Overview](#overview)
2. [System Architecture](#system-architecture)
3. [Agent Pipeline](#agent-pipeline)
   - [Agent 1 — Router](#agent-1--router)
   - [Agent 2 — Aggregator](#agent-2--aggregator)
   - [Agent 3 — Narrative Generator](#agent-3--narrative-generator)
   - [Agent 4 — Validator](#agent-4--validator)
   - [Agent 5 — PDF Filer](#agent-5--pdf-filer)
4. [Knowledge Base Architecture](#knowledge-base-architecture)
5. [Supported Report Types](#supported-report-types)
6. [Repository Structure](#repository-structure)
7. [Getting Started](#getting-started)
8. [Configuration](#configuration)
9. [API Reference](#api-reference)
10. [Frontend — Streamlit Dashboard](#frontend--streamlit-dashboard)
11. [PDF Generation Pipeline](#pdf-generation-pipeline)
12. [Development Notes](#development-notes)

---

## Overview

This system automates the full lifecycle of U.S. financial compliance filings mandated by the Bank Secrecy Act (BSA) and OFAC regulations. Raw transaction data enters the pipeline and a filled, regulation-ready PDF exits — with no manual form-filling required.

| Report | Regulation | Form |
|--------|-----------|------|
| **SAR** — Suspicious Activity Report | BSA / 31 U.S.C. § 5318 | FinCEN Form 111 |
| **CTR** — Currency Transaction Report | BSA / 31 U.S.C. § 5313 | FinCEN Form 112 |
| **OFAC Rejection Report** | 31 C.F.R. Part 501 | OFAC Rejected Transactions Form |

The pipeline handles the entire workflow:

- **Routing** — determines which report type(s) a case requires
- **Aggregation** — structures raw case data into typed compliance schemas
- **Narrative generation** — writes the mandatory narrative paragraph, grounded in a curated knowledge base of regulatory examples
- **Validation** — checks completeness, regulatory alignment, and narrative quality before filing
- **PDF filing** — fills AcroForm fields in the official PDF templates and writes the final output

A Streamlit dashboard provides a human-in-the-loop interface for case submission, status monitoring, and report download.

---

## System Architecture

<p align="center">
  <img src="img/system_architecture.png" alt="System Architecture Diagram" width="92%"/>
</p>

The system is built across four layers:

| Layer | Technology |
|-------|-----------|
| **Presentation** | Streamlit multi-page dashboard · FastAPI REST endpoints |
| **Orchestration** | `backend/orchestration/crew.py` — `create_compliance_crew()` |
| **Intelligence** | Five CrewAI agents · GPT-4o (routing, aggregation, validation) · GPT-4o-mini (narrative) |
| **Data** | Supabase PostgreSQL · Weaviate vector DB · Redis cache · Supabase Storage (PDF templates) |

---

## Agent Pipeline

All agents are coordinated by [`backend/orchestration/crew.py`](backend/orchestration/crew.py). The function `create_compliance_crew(transaction_data, on_stage)` runs the full pipeline and streams progress via the `on_stage(agent_name, pct)` callback.

```
Raw case input (JSON / form submission)
        │
        ▼
  normalize_case_data()               ← deterministic field normalisation
        │
        ├──── OFAC detected? ──────────────────────────────────┐
        │     (deterministic, no LLM)                          │
        ▼                                                       ▼
  Agent 1: Router                                  route → OFAC_REJECT
  GPT-4o classifies report types                  (skip LLM call)
        │                                                       │
        └────────────────────┬─────────────────────────────────┘
                             │
                   report_types resolved:
                   SAR · CTR · OFAC_REJECT · SAR+CTR · none
                             │
               ┌─────────────┴─────────────┐
               │ no report types           │ has report types
               ▼                           ▼
       NO_FILING_REQUIRED         Agent 2: Aggregator   (15 → 35%)
                                  Structures data into typed schema
                                           │
                                  ┌────────┴────────┐
                             CTR only         SAR or OFAC
                             (no narrative)        │
                                                   ▼
                                        Agent 3: Narrative   (35 → 55%)
                                        KB-grounded paragraph
                                                   │
                                                   ▼
                                        Agent 4: Validator   (55 → 75%)
                                        3-tier compliance check
                                                   │
                                     ┌─────────────┴─────────────┐
                                  APPROVED                 NEEDS_REVIEW
                                     │                    / REJECTED
                                     ▼                         │
                              Agent 5: PDF Filer (75 → 100%)   │
                              Fill AcroForm · write PDF         ▼
                                                         return validation
                                                         report (no PDF)
                             │
                             ▼
                    Final result returned:
                    router · aggregator · narrative · validation · final PDF
```

---

### Agent 1 — Router

**File:** [`backend/agents/router_agent.py`](backend/agents/router_agent.py)  
**Model:** GPT-4o (SAR/CTR only; OFAC is deterministic)  
**Progress:** 0 → 15%

Determines which report type(s) the case requires. OFAC cases are detected deterministically from the case payload (checking `report_type_code`, `date_of_rejection`, sanctions keywords) — no LLM call is made. For all other cases a CrewAI agent performs the classification.

**Routing logic:**

| Condition | Report type |
|-----------|------------|
| `date_of_rejection` present, or `OFAC`/`SDN`/`SANCTION` in rejection reason | `OFAC_REJECT` |
| Total cash activity ≥ $10,000 | `CTR` |
| Suspicious activity indicators present | `SAR` |
| Both cash threshold and suspicious activity | `CTR + SAR` |
| Neither | No filing required |

**Output:**
```json
{
  "report_types": ["SAR"],
  "confidence_score": 0.97,
  "reasoning": "SAR required because suspicious activity indicators are present.",
  "total_cash_amount": 25500.0,
  "kb_status": "EXISTS"
}
```

---

### Agent 2 — Aggregator

**File:** [`backend/agents/aggregator_agent.py`](backend/agents/aggregator_agent.py)  
**Model:** GPT-4o  
**Progress:** 15 → 35%

Runs for each identified report type. The `AggregatorOrchestrator` extracts and normalises case fields into a typed Pydantic schema, computes a risk score, and flags any missing required fields.

**SAR output — `SARCaseSchema`:**
```
case_id · customer_name · subject · institution
SuspiciousActivityInformation · suspicious_activity_type
activity_date_range · total_amount_involved · transactions
risk_flags · risk_score · missing_required_fields
narrative_required · narrative_justification
```

**CTR output — `CTRCaseSchema`:**
```
case_id · subject · institution · section_a · section_b
transaction · total_amount_involved · transaction_count
```

**OFAC output** (built inline, no LLM):
```
institution_name · amount_rejected · currency · sanctions_program
date_of_rejection · originator_name · beneficiary_fi
preparer_name · preparer_title
```

---

### Agent 3 — Narrative Generator

**File:** [`backend/agents/narrative_agent.py`](backend/agents/narrative_agent.py)  
**Model:** GPT-4o-mini (temperature 0.2)  
**Progress:** 35 → 55%  
**Skipped for:** CTR (no narrative required)

Generates the mandatory narrative section. Before writing, the agent fetches two artefacts live from Supabase:

1. **Narrative instructions** — report-type-specific writing guidelines (`report_types` table)
2. **Reference examples** — up to 8 annotated examples with effectiveness notes (`narrative_examples` table)

Both are injected verbatim into the task prompt, ensuring factual accuracy and FinCEN-approved phrasing. The agent is explicitly instructed not to hallucinate — it may only use facts present in the input JSON.

**Output:**
```json
{
  "narrative_text": "On April 2, 2026, the institution identified...",
  "word_count": 172,
  "character_count": 1215,
  "key_points_covered": ["who", "what", "when", "where", "why", "how"],
  "regulations_cited": ["31 USC 5318"]
}
```

**SAR narrative requirements:** who/what/when/where/why/how · no legal conclusions · neutral tone (`"appears inconsistent"`, `"raises concern"`)

**OFAC narrative requirements:** transaction was *rejected* (not blocked or frozen) · specific sanctions nexus identified · documents reviewed described · records retained confirmed

---

### Agent 4 — Validator

**File:** [`backend/agents/validator_agent.py`](backend/agents/validator_agent.py)  
**Model:** GPT-4o  
**Progress:** 55 → 75%

Performs a three-tier compliance review. Uses two CrewAI tools:
- `get_validation_rules_tool` — fetches report-type-specific rules from Supabase
- `search_kb_tool` — searches the Weaviate knowledge base for regulatory guidance

| Tier | What is checked |
|------|----------------|
| **Technical** | All required fields present and correctly formatted |
| **Regulatory** | BSA/FinCEN alignment · filing deadline compliance · mandatory elements |
| **Quality** | Narrative clarity · specificity · appropriate tone · sufficient length |

**Output:**
```json
{
  "status": "APPROVED",
  "completeness_score": 95.0,
  "compliance_checks": {
    "required_fields": "PASS",
    "bsa_compliance": "PASS",
    "fincen_guidelines": "PASS"
  },
  "narrative_quality_score": 8.5,
  "issues": [],
  "recommendations": [],
  "approval_flag": true
}
```

> **Development shortcut:** set `SKIP_VALIDATOR_FOR_TESTING=true` in `.env` to bypass this agent and assume `APPROVED`.

---

### Agent 5 — PDF Filer

**Files:** [`backend/pdf_filler/`](backend/pdf_filler/) · [`backend/tools/pdf_tools.py`](backend/tools/pdf_tools.py) · [`backend/tools/ai_field_verifier.py`](backend/tools/ai_field_verifier.py)  
**Progress:** 75 → 100%  
**No LLM involved** — fully deterministic

Fetches the official PDF template and AcroForm field mapping from Supabase, resolves each mapped key against the case data via dot-path traversal, optionally cross-checks critical text fields using a GPT-4o verifier, then writes the filled PDF to `outputs/`.

**Dot-path resolution example:**
```
mapping key: "institution.name"
resolves to: data["institution"]["name"]
```

**AI field verifier** (`backend/tools/ai_field_verifier.py`): a lightweight GPT-4o call that compares names, institution, and narrative in the mapped fields against the source case data and applies corrections before the PDF is written. Fail-safe — any error returns the original values unchanged.

**Special handling — SAR Form 111 item 26 (Amount Involved):**  
The `Text9` AcroForm field is shared across 31 widget instances on the form (items 26, 28, 63, phone). Setting it normally causes all instances to display the same digit. `_fix_item26_cells()` splits the 7 item-26 widgets into independent field nodes via `pypdf` so each digit cell can be set individually.

**Special handling — OFAC forms:**  
`_build_ofac_pdf_payload()` remaps the OFAC case structure (`institution_information`, `transaction_information`, `preparer_information`) to the nested `institution.*` / `transaction.*` / `preparer.*` key paths that the Supabase field mapping expects.

---

## Knowledge Base Architecture

<p align="center">
  <img src="img/knowledgebase_architecture.png" alt="Knowledge Base Architecture Diagram" width="92%"/>
</p>

The knowledge base spans three storage systems, each serving a distinct role in the pipeline.

### Supabase — PostgreSQL (Structured Data)

Managed via SQLAlchemy ORM in [`backend/knowledge_base/supabase_client.py`](backend/knowledge_base/supabase_client.py).

| Table | Used by | Purpose |
|-------|---------|---------|
| `report_types` | Agent 3, Agent 5 | Narrative instructions · JSON schema · PDF template URL |
| `narrative_examples` | Agent 3 | Annotated example narratives with effectiveness notes |
| `validation_rules` | Agent 4 | Per-report rules: `critical` / `warning` / `quality` |
| `field_mappings` | Agent 5 | JSON dotted-path → AcroForm field ID per report type |
| `risk_indicators` | Agent 2 | Risk detection patterns (structuring, layering, smurfing) |
| `job_status` | API layer | Async job tracking: status · progress · current agent · result |
| `audit_log` | All agents | Immutable compliance audit trail |

### Weaviate — Vector DB (Semantic Search)

Client: [`backend/knowledge_base/weaviate_client.py`](backend/knowledge_base/weaviate_client.py)

| Collection | Content | Used by |
|-----------|---------|---------|
| `narratives` | Historical SAR/CTR/OFAC narratives with activity-type metadata | Agent 3 |
| `regulations` | BSA, AML, OFAC regulatory guidance text | Agent 4 |
| `definitions` | Compliance glossary | Agent 4 |

### Redis — Cache

LRU cache with 300 s TTL in front of both Supabase and Weaviate. Reduces repeated KB round-trips within a single pipeline run. Degrades gracefully — the system continues normally if Redis is unavailable.

---

## Supported Report Types

### SAR — Suspicious Activity Report

**Triggered by** any of: structuring · smurfing · money laundering (layering/integration) · wire/check fraud · identity theft · account takeover · unusual transaction patterns inconsistent with customer profile

**Required fields:** subject identity · institution · suspicious activity categories · activity date range · amount involved · narrative paragraph

### CTR — Currency Transaction Report

**Triggered by** total cash-in or cash-out ≥ **$10,000** in a single business day

**Required fields:** subject/business identity · institution · transaction amounts by cash type · transaction date

### OFAC Rejection Report

**Triggered by** a transaction rejected due to a sanctions match

**Required fields:** institution info · originator & beneficiary · amount rejected · transaction date · rejection date · OFAC program or reason · preparer information · narrative explaining the rejection basis

---

## Repository Structure

```
Agentic_BNY_AI_Compliance/
│
├── backend/                          # All server-side application code
│   ├── orchestration/
│   │   └── crew.py                   # Pipeline entry point — create_compliance_crew()
│   │
│   ├── agents/
│   │   ├── router_agent.py           # Agent 1 — report type classification
│   │   ├── aggregator_agent.py       # Agent 2 — data aggregation & schema building
│   │   ├── narrative_agent.py        # Agent 3 — narrative generation
│   │   └── validator_agent.py        # Agent 4 — compliance validation
│   │
│   ├── api/
│   │   ├── main.py                   # FastAPI app initialisation + CORS
│   │   ├── routes.py                 # Endpoints: submit · status · download · list
│   │   └── schemas.py                # Pydantic request/response models
│   │
│   ├── tools/
│   │   ├── kb_tools.py               # CrewAI tools: search_kb · get_validation_rules
│   │   ├── pdf_tools.py              # SARReportFiler · CTRReportFiler
│   │   ├── field_mapper.py           # normalize_case_data · SARFieldMapper · CTRFieldMapper
│   │   └── ai_field_verifier.py      # GPT-4o field cross-check before PDF write
│   │
│   ├── pdf_filler/
│   │   ├── fill_engine.py            # AcroForm fill: dot-path resolution · field writing
│   │   ├── sar_mapping.py            # SAR PDF mapping parser (new-format / legacy)
│   │   ├── sar_flatten.py            # Flatten nested SAR JSON to flat FinCEN keys
│   │   ├── agent.py                  # PdfFillerAgent: template fetch + fill orchestration
│   │   └── metadata.py               # Supabase template/mapping fetch helpers
│   │
│   ├── knowledge_base/
│   │   ├── supabase_client.py        # SQLAlchemy ORM · job tracking · audit log
│   │   ├── weaviate_client.py        # Weaviate vector search client
│   │   └── kb_manager.py            # Unified KB interface with Redis caching
│   │
│   ├── config/
│   │   └── settings.py               # Pydantic settings: DB · LLM · Weaviate · Redis
│   │
│   └── tests/
│       └── test_pdf_filler_unit.py   # Unit tests for PDF fill engine
│
├── streamlit_app/                    # Frontend dashboard
│   ├── app.py                        # Landing page — KPI cards · recent activity
│   ├── pages/
│   │   ├── 1_Dashboard.py            # Case volume · status charts · processing times
│   │   ├── 2_Submit_Case.py          # Case submission: form · JSON · sample data
│   │   ├── 3_Case_Management.py      # Case list · detail view · validation report
│   │   ├── 4_Reports.py              # PDF download · narrative preview
│   │   ├── 5_Knowledge_Base.py       # Browse examples · regulations · rules
│   │   └── 6_Settings.py            # API URL · model · timeouts · cache
│   ├── components/
│   │   ├── header.py                 # App header with logo (img/logo.png)
│   │   ├── sidebar.py                # Navigation + backend health + logo
│   │   ├── metric_card.py            # KPI card widget
│   │   ├── agent_timeline.py         # Visual progress bar through agents
│   │   ├── report_preview.py         # Report summary view
│   │   └── status_badge.py           # Case status indicator
│   └── utils/
│       ├── api_client.py             # HTTP client for FastAPI backend
│       ├── session_state.py          # Session state initialisation
│       ├── formatting.py             # Currency · date helpers
│       └── validators.py             # Client-side form validation
│
├── data/
│   ├── input_data/                   # Example case JSON inputs
│   ├── raw_data_pdf/                 # Local PDF templates (SAR.pdf · CTR.pdf)
│   ├── sanctions/                    # 30 OFAC rejection test scenarios
│   └── validation_data/             # Local rule definitions (offline fallback)
│
├── examples/                         # Sample JSON payloads for all report types
├── docs/
│   ├── NARRATIVE_VALIDATION.md       # Narrative QA criteria and prohibited phrases
│   └── OFAC_REJECT.md                # OFAC pipeline specification
│
├── scripts/                          # Ingestion and utility scripts
├── notebooks/                        # Jupyter notebooks
├── img/
│   ├── logo.png                      # Application logo
│   ├── system_architecture.png       # System architecture diagram
│   └── knowledgebase_architecture.png
│
├── outputs/                          # Generated PDFs — git-ignored
├── archive/                          # Legacy pre-refactor code — reference only
│
├── .env.example                      # Environment variable template
├── .gitignore
├── pyproject.toml
└── requirements.txt
```

---

## Getting Started

### Prerequisites

- Python 3.12+
- [Supabase](https://supabase.com) project (PostgreSQL + REST API)
- [Weaviate](https://weaviate.io) instance (cloud or local Docker)
- OpenAI API key
- Redis (optional — system continues without it)

### Installation

```bash
# Clone the repository
git clone https://github.com/tursunait/Agentic_BNY_AI_Compliance.git
cd Agentic_BNY_AI_Compliance

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env — see Configuration section below
```

### Running

```bash
# Terminal 1 — FastAPI backend (port 8000)
uvicorn backend.api.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2 — Streamlit frontend (port 8501)
python -m streamlit run streamlit_app/app.py --server.port 8501
```

| Interface | URL |
|-----------|-----|
| Streamlit dashboard | http://localhost:8501 |
| FastAPI Swagger docs | http://localhost:8000/docs |

---

## Configuration

All settings are loaded from environment variables via [`backend/config/settings.py`](backend/config/settings.py).

### Required

```bash
# LLM
OPENAI_API_KEY=sk-...

# Supabase — PostgreSQL connection (choose Option A or B)
# Option A: single DSN
SUPABASE_DB_URL=postgresql://postgres:password@host:5432/postgres?sslmode=require

# Option B: individual components
SUPABASE_DB_HOST=db.<project>.supabase.co
SUPABASE_DB_PASSWORD=<password>
SUPABASE_DB_USER=postgres
SUPABASE_DB_PORT=5432
SUPABASE_DB_NAME=postgres

# Supabase REST API (narrative guidance, PDF templates)
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_ANON_KEY=eyJ...

# Weaviate
WEAVIATE_URL=https://<cluster>.weaviate.cloud
WEAVIATE_API_KEY=<key>
```

### Optional

```bash
REDIS_URL=redis://localhost:6379/0     # Caching — omit to disable
SKIP_VALIDATOR_FOR_TESTING=false       # true → skip Agent 4, assume APPROVED
```

### Database URL priority

The system tries these in order until one resolves:
1. `SUPABASE_DB_URL`
2. Components: `SUPABASE_DB_HOST` + `SUPABASE_DB_PASSWORD` + rest
3. `SUPABASE_URL` (if it is a `postgresql://` URI)
4. `DATABASE_URL`

---

## API Reference

Base path: `/api/v1`

### `POST /reports/submit`

Submit a case for asynchronous processing. Returns immediately with a `job_id`.

```json
// Request
{ "transaction_data": { "case_id": "CASE-001", "subject": { ... }, ... } }

// Response
{ "job_id": "3d6abc52-...", "status": "pending", "message": "Report generation started" }
```

### `GET /reports/{job_id}/status`

Poll job progress. Repeat until `status` is `"completed"` or `"failed"`.

```json
// In progress
{ "status": "processing", "current_agent": "narrative", "progress": 55, "result": null }

// Complete
{ "status": "completed", "progress": 100, "result": { "final": { "pdf_path": "..." } } }
```

### `GET /reports/{job_id}/download`

Stream the generated PDF as a file download.  
Query param: `report_type=SAR|CTR` (required only when both were generated for one case).

### `GET /cases/list`

List all jobs. Query params: `status` · `report_type` · `query` (full-text search).

### `GET /health`

```json
{ "status": "healthy", "services": { "database": true, "weaviate": true, "redis": true } }
```

---

## Frontend — Streamlit Dashboard

| Page | Path | Description |
|------|------|-------------|
| **Home** | `app.py` | KPI overview and recent activity |
| **Dashboard** | `pages/1_Dashboard.py` | Case volume · status distribution · processing times |
| **Submit Case** | `pages/2_Submit_Case.py` | Three input modes: form · JSON paste · sample data |
| **Case Management** | `pages/3_Case_Management.py` | Full case list · detail view · validation report |
| **Reports** | `pages/4_Reports.py` | PDF download · narrative preview |
| **Knowledge Base** | `pages/5_Knowledge_Base.py` | Browse examples · regulations · validation rules |
| **Settings** | `pages/6_Settings.py` | API URL · model · timeouts · cache |

The logo (`img/logo.png`) is displayed on every page — in the top header bar and at the top of the sidebar — via `streamlit_app/components/header.py` and `streamlit_app/components/sidebar.py`.

---

## PDF Generation Pipeline

```
1. Fetch from Supabase
   → pdf_template_path  (URL to official PDF form)
   → pdf_field_mapping  (JSON dotted-path → AcroForm field ID)

2. Download and cache PDF template locally

3. Resolve field values via dot-path traversal
   _get_nested(data, "institution.name")  →  data["institution"]["name"]
   Narrative keys (narrative_text / narrative / __narrative__)  →  narrative_text param
   Shallow fallback  →  tries transaction.X, institution.X, subject.X

4. AI field verification  (GPT-4o, fail-safe)
   Checks names · institution · narrative against source data
   Any error → silently returns original values unchanged

5. Fill AcroForm
   pypdf PdfWriter.update_page_form_field_values()
   NeedAppearances = True  (ensures values render in all PDF viewers)

6. SAR Form 111 — item 26 special handling
   Text9 is shared across 31 widget instances
   _fix_item26_cells() splits 7 item-26 digit widgets into
   independent AcroForm nodes so each dollar digit is set individually

7. Write output
   → outputs/{REPORT_TYPE}_{timestamp}.pdf
```

---

## Development Notes

### Adding a New Report Type

1. Insert a row into Supabase `report_types` — set `report_type_code`, `narrative_instructions`, `json_schema`, `pdf_template_path`
2. Add field mappings to `field_mappings`
3. Add annotated examples to `narrative_examples` with `effectiveness_notes`
4. Add validation rules to `validation_rules`
5. Extend `_normalize_report_types()` in `crew.py` to accept the new code
6. Add detection logic to `_is_ofac_case()` if deterministic routing is needed

### Improving Narrative Quality

Narrative output is driven entirely by the content in Supabase — **no code changes needed**. Add high-quality rows to `narrative_examples` with detailed `effectiveness_notes`. The generator fetches up to 8 examples per run and injects them directly into the LLM prompt.

### Running Tests

```bash
pytest backend/tests/ -v
```

### Skipping the Validator in Development

```bash
SKIP_VALIDATOR_FOR_TESTING=true uvicorn backend.api.main:app --reload
```

---

<p align="center">
  Built for the BNY Mellon Capstone — Agentic AI Compliance Automation
</p>
