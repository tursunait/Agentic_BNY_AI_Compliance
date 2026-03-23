# Validator Agent – SAR & CTR Compliance Checker

This project implements a **Validator Agent** designed to automatically validate Suspicious Activity Reports (SAR) and Currency Transaction Reports (CTR) against a comprehensive set of regulatory rules and quality metrics. The agent processes input JSON files, runs rule‑based checks, optionally invokes a Large Language Model (LLM) to evaluate narrative quality, and produces a detailed validation report with scores and recommendations.

---

## Table of Contents
- [Overview](#overview)
- [System Architecture](#system-architecture)
- [Project Structure](#project-structure)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Input Data Format](#input-data-format)
- [Validation Rules](#validation-rules)
- [Output Format](#output-format)
- [LLM Integration](#llm-integration)
- [Extending the Agent](#extending-the-agent)
- [License](#license)

---

## Overview

Financial institutions are required to file SAR and CTR reports under the Bank Secrecy Act (BSA) and related regulations. The **Validator Agent** automates the quality control of these reports by:

- Checking completeness of required fields.
- Ensuring compliance with regulatory requirements (BSA, AML, OFAC).
- Validating data accuracy (e.g., TIN format, amount consistency).
- Assessing narrative quality (for SAR) using an LLM (ZhiPu GLM‑5).

The agent outputs a structured JSON report containing a pass/fail decision, an overall validation score, per‑category scores, and a human‑readable summary.

---

## System Architecture

```
┌─────────────────┐     ┌─────────────────────────────────────┐     ┌─────────────────┐
│  Input JSON     │────▶│         Validator Agent             │────▶│  Output JSON    │
│  (SAR or CTR)   │     │  (main.py + rule engines + LLM)     │     │  (validation    │
└─────────────────┘     └─────────────────────────────────────┘     │   report)       │
                                                                └─────────────────┘
         ▲                                        │
         │                                        │
         │ reads                                  │ writes
         │                                        ▼
┌─────────────────┐                      ┌─────────────────┐
│ Validation Data │                      │  Output Data    │
│ (rules, legal   │                      │  (case_id.json) │
│  requirements)  │                      └─────────────────┘
└─────────────────┘
```

- **Input**: A single JSON file placed in `data/input_data/` – either a CTR or a SAR report.
- **Validation Data**: Two JSON files (`validation_rules.json` and `legal_requirements.json`) that define the checks to be performed.
- **Rule Engines**: Separate engines for CTR and SAR that implement the actual checks.
- **LLM Evaluator**: Invoked for SAR narratives to score quality and identify missing elements.
- **Output**: A JSON file placed in `data/output_data/` containing the validation result and a detailed text report.

---

## Project Structure

```
.
├── .env                         # Environment variables (LLM API keys)
├── requirements.txt             # Python dependencies
├── main.py                      # Entry point
├── utils/
│   ├── __init__.py
│   ├── ctr_rules_engine.py      # CTR‑specific rule implementations
│   ├── sar_rules_engine.py      # SAR‑specific rule implementations
│   ├── llm_evaluator.py         # LLM narrative evaluation
│   └── scoring.py               # Scoring and status determination
└── data/
    ├── input_data/              # Place input JSON files here
    ├── output_data/             # Generated validation reports
    └── validation_data/         # Rule definitions (JSON + CSV)
        ├── legal_requirements.json
        ├── legal_requirements.csv
        ├── validation_rules.json
        └── validation_rules.csv
```

---

## Installation

1. **Clone the repository** (if applicable) or copy the files to your working directory.
2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Set up environment variables**:
   Create a `.env` file in the project root with the following content (replace `your_api_key_here` with your actual key):
   ```ini
   DEFAULT_LLM_API_KEY=your_api_key_here
   DEFAULT_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
   DEFAULT_LLM_MODEL_NAME=glm-5
   ```

---

## Configuration

All validation rules are defined in `data/validation_data/validation_rules.json`. Each rule has the following structure:

```json
{
  "rule_id": "CTR-REQ-001",
  "report_type": "CTR",
  "severity": "critical",
  "rule_json": {
    "condition": "The total cash-in amount or total cash-out amount must exceed $10,000.",
    "message": "Transaction amount does not exceed the $10,000 reporting threshold."
  },
  "legal_refs": ["LEG-CTR-001"]
}
```

The corresponding legal requirements (for reference) are stored in `legal_requirements.json`.

> **Note**: The agent reads only `validation_rules.json`; `legal_requirements.json` is provided for auditability and can be ignored by the engine.

---

## Usage

1. **Place your input JSON file** (either a CTR or SAR report) inside `data/input_data/`.  
   The agent will process the **first** JSON file it finds (alphabetical order).  
   Example files (`ctr_output.json`, `sar_output.json`) are provided as templates.

2. **Run the validator**:
   ```bash
   python main.py
   ```

3. **Check the output**:
   The validation report will be written to `data/output_data/` with the same filename as the input’s `case_id` (e.g., `CTR-2024-00001.json`).

---

## Input Data Format

### CTR Example (`ctr_output.json`)

```json
{
  "report_type": "CTR",
  "case_id": "CTR-2024-00001",
  "amends_prior": false,
  "multiple_persons": false,
  "multiple_transactions": false,
  "section_a": { ... },
  "section_b": { ... },
  "transaction": { ... },
  "institution": { ... },
  "signature": { ... },
  "created_at": "...",
  "data_sources": [...],
  "missing_required_fields": [],
  "data_quality_issues": [],
  "risk_score": 0.0
}
```

### SAR Example (`sar_output.json`)

```json
{
  "report_type": "SAR",
  "case_id": "SAR-2024-00001",
  "filing_type": "initial",
  "prior_report_number": null,
  "subject": { ... },
  "activity": { ... },
  "financial_institution": { ... },
  "filing_institution": { ... },
  "narrative": null,
  "created_at": "...",
  "data_sources": [...],
  "missing_required_fields": [],
  "data_quality_issues": [],
  "risk_score": 50.0,
  "narrative_required": true
}
```

All fields follow the structure defined in the FinCEN forms. Any `null` values will be treated as missing data and may trigger rule violations.

---

## Validation Rules

The rule engine implements two separate classes:

- `CTRRuleChecker` (in `ctr_rules_engine.py`)
- `SARRuleChecker` (in `sar_rules_engine.py`)

Each rule ID (e.g., `CTR-REQ-001`) is mapped to a method `check_ctr_req_001()` inside the respective engine. If a rule is not implemented, the engine adds a violation indicating the missing implementation (this is intentional to avoid silent failures).

Rules cover four categories:

| Category      | Description                                                                 |
|---------------|-----------------------------------------------------------------------------|
| Completeness  | All mandatory fields are present (e.g., name, address, TIN, ID details).    |
| Compliance    | Adherence to regulatory requirements (BSA, AML, OFAC, red flags).           |
| Accuracy      | Correct data formats (TIN, dates, amounts) and internal consistency.        |
| Narrative     | Quality of the SAR narrative (assessed by LLM for SAR, always 100 for CTR). |

Scores are calculated per category using **weighted severity**:
- critical = 5
- high = 3
- medium = 1
- low = 0.5

The overall score is the average of the four category scores.

---

## Output Format

The output JSON contains the following fields:

```json
{
  "case_id": "CTR-2024-00001",
  "report_type": "CTR",
  "status": "APPROVED | NEEDS_REVIEW | REJECTED",
  "pass_or_not": "Yes | No",
  "validation_score": 87.5,
  "scores": {
    "completeness": 80.0,
    "compliance": 90.0,
    "accuracy": 95.0,
    "narrative": 85.0
  },
  "validation_report": "====================================\nVALIDATION REPORT\n... (detailed text) ...",
  "generate_times": 1,
  "current_best_score": 0.0
}
```

- **status**: Determined by the presence of any **critical** violation (`REJECTED`), or by the overall score (≥80 → `APPROVED`, otherwise `NEEDS_REVIEW`).
- **validation_score**: Average of the four category scores.
- **scores**: Individual scores for completeness, compliance, accuracy, and narrative.
- **validation_report**: A multi‑line human‑readable summary listing all violations, recommendations, and category scores.

---

## LLM Integration

For SAR reports, if a narrative is present and required (`narrative_required: true`), the agent calls the ZhiPu GLM‑5 API to evaluate the narrative quality. The prompt asks the LLM to check for:

- The five Ws (who, what, when, where, why) and how.
- Prohibited phrases (e.g., “see attached”).
- Clear description of funds flow.

The LLM returns a JSON with:
- `score` (0–100)
- `missing_elements` (list of strings)
- `comments` (brief feedback)

The score overrides the default narrative category score, and any missing elements or comments are added as violations (severity medium/low) so they appear in the final report.

If the API call fails, a default score of 50 is used and an error comment is added.

---

## Extending the Agent

### Adding a New Rule
1. Edit `data/validation_data/validation_rules.json` and add a new rule with a unique `rule_id`, e.g., `CTR-REQ-030`.
2. In the corresponding rule engine (`ctr_rules_engine.py` or `sar_rules_engine.py`), implement a method named `check_ctr_req_030(self) -> Tuple[bool, str]` that returns `(True, None)` if the rule passes, or `(False, custom_message)` if it fails.
3. The engine’s `check_all()` will automatically call the method.

### Modifying Scoring Categories
The category mapping is handled in `scoring.py` by the `categorize_rule(rule_id)` function. Adjust the keyword‑based logic to reclassify rules as needed.

### Changing the LLM Provider
Update the `.env` variables and modify `llm_evaluator.py` to match the new API endpoint and expected response format.
