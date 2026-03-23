## Narrative Generator Agent (SAR & OFAC Rejected Transaction)

A **CrewAI** agent that generates regulatory narratives from structured JSON input. It supports **SAR** (Suspicious Activity Reports) and **OFAC_REJECT** (sanctions rejected transaction reports) through a single entry point: report type is detected from the input or passed explicitly, then the correct knowledge-base guidance is retrieved and the narrative is generated and validated.

The agent is designed to **not hallucinate**: it uses only the provided input and follows narrative instructions and examples from a Supabase knowledge base (or local fallback for OFAC_REJECT).

### Features

- **Report types**: **SAR** and **OFAC_REJECT**. Routing by `report_type_code` in the input (or argument); same API for both.
- **Input**:  
  - **SAR**: `case_id`, `subject`, `SuspiciousActivityInformation`, plus optional alert, transactions, institution, etc.  
  - **OFAC_REJECT**: `case_id`, `transaction`, `case_facts`, plus optional institution, preparer, `report_type_code`.
- **Output**: Same structure as input with one new field `narrative` (input JSON + `{"narrative": "..."}`).
- **Knowledge base**: Fetches narrative instructions and examples from Supabase `report_types` and `narrative_examples` by `report_type_code`. For **OFAC_REJECT**, if the KB has no row (or Supabase is unavailable), built-in local guidance and an example are used so the flow works without Supabase.
- **Narrative validation**: Optional checks per report type (structure, tone, completeness). See `validate_narrative()` and `docs/NARRATIVE_VALIDATION.md`; OFAC_REJECT checks include rejection stated, no blocking language, sanctions nexus and documents reviewed.
- **CrewAI**: One agent per run (role/goal from report type registry); one task; uses OpenAI `gpt-4o-mini`.
- **Tests**: Pytest for schemas, agent, KB fallback, OFAC_REJECT routing and validation (mocked so no API key needed for unit tests).
- **Demo**: Jupyter notebook (SAR + OFAC sections) from input JSON → generate → validate → display.

### Setup

From the project root:

```bash
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt   # or pip install -e .
```

Set your OpenAI and (for SAR) Supabase environment variables:

```bash
export OPENAI_API_KEY=sk-...
export SUPABASE_URL="https://ggxnbctgyiitfwxharjt.supabase.co"
export SUPABASE_ANON_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

The Supabase URL and anon key must have read access to:

- `report_types` (including `narrative_instructions`, `json_schema`, `validation_rules`, `pdf_template_path`, and `pdf_field_mapping`)
- `narrative_examples` (including `summary`, `narrative_text`, `effectiveness_notes`, and `example_order`)

### Running the agent

From the project root, with `src` on the Python path:

**SAR** (pass `report_type_code` or omit; SAR is default when not in input):

```bash
cd narrative_agent   # or project root
PYTHONPATH=src python -c "
from narrative_agent import generate_narrative
import json
with open('examples/input_example.json') as f:
    data = json.load(f)
out = generate_narrative(data, report_type_code='SAR')
print(json.dumps(out, indent=2))
"
```

**OFAC rejected transaction** (include `report_type_code` in input or pass it):

```bash
PYTHONPATH=src python -c "
from narrative_agent import generate_narrative, validate_narrative
import json
with open('examples/ofac_reject_example.json') as f:
    data = json.load(f)
out = generate_narrative(data)   # routes by data['report_type_code']
v = validate_narrative(out['narrative'], out, report_type_code='OFAC_REJECT')
print('Valid:', v.passed)
print(out['narrative'][:300], '...')
"
```

Or use the class:

```python
from narrative_agent import NarrativeGeneratorCrew

crew = NarrativeGeneratorCrew(verbose=True)
result = crew.kickoff(inputs=<your_input_dict>)  # SAR or OFAC_REJECT shape
# result is input JSON + {'narrative': '...'}
```

Internally, the agent:

- Resolves `report_type_code` from the input (e.g. `report_type_code` or `report_type`) or the argument; default is `SAR`.
- Fetches narrative instructions and examples from Supabase `report_types` and `narrative_examples` for that code. For **OFAC_REJECT**, if the KB has no row or Supabase is unavailable, local guidance from the report type registry is used.
- Builds the prompt from retrieved (or local) instructions, examples, and the full structured input, then generates one narrative paragraph and returns input + `narrative`.

### Running tests

```bash
cd narrative_agent   # or project root
PYTHONPATH=src pytest tests/ -v
```

Tests cover schema validation (SAR and OFAC_REJECT required keys), agent orchestration and routing, KB fallback for OFAC_REJECT, and narrative validation. Agent and KB calls are mocked so no API keys are required for unit tests.

### Jupyter notebook demo

From the project root:

```bash
PYTHONPATH=src jupyter notebook notebooks/sar_narrative_demo.ipynb
```

Run all cells. The notebook includes:

- **SAR**: Load example input, run the agent, validate the narrative, display result (Sections 1–7).
- **OFAC_REJECT** (Section 8): Load `examples/ofac_reject_example.json`, generate narrative (routing by `report_type_code`), validate, and display. Set `OPENAI_API_KEY` before running agent cells.

### Project structure

```text
Agentic_BNY_AI_Compliance/   # or narrative_agent/
  README.md
  requirements.txt
  examples/
    input_example.json        # SAR input
    input_example2.json
    input_example3.json
    ofac_reject_example.json  # OFAC_REJECT input
  docs/
    NARRATIVE_VALIDATION.md   # Narrative quality criteria and process
    OFAC_REJECT.md            # OFAC_REJECT routing, KB, validation, example
  src/
    narrative_agent/
      __init__.py
      agent.py                # generate_narrative(), create_crew(); report-type routing
      schemas.py              # NarrativeOutput, validate_input (report-type-aware)
      report_types.py         # Registry: SAR, OFAC_REJECT (required keys, agent role, local guidance)
      knowledge_base.py       # Supabase report_types + narrative_examples; OFAC_REJECT fallback
      narrative_validation.py # validate_narrative() per report type (SAR / OFAC_REJECT)
      examples.py             # Local few-shot (legacy)
      narrative_reference.py  # Local SAR guidelines (legacy)
  tests/
    test_schemas.py
    test_agent.py
    test_knowledge_base.py
    test_ofac_reject.py       # OFAC_REJECT routing, validation, sample JSON
  notebooks/
    sar_narrative_demo.ipynb  # SAR (1–7) + OFAC_REJECT (8), validation
```

### Verifying Supabase connectivity

To verify the knowledge base for SAR:

```bash
PYTHONPATH=src python -c "
from narrative_agent.knowledge_base import fetch_report_type_config, fetch_narrative_examples
cfg = fetch_report_type_config('SAR')
examples = fetch_narrative_examples('SAR')
print('Report type:', cfg.report_type_code, '-', cfg.display_name)
print('Instructions snippet:', (cfg.narrative_instructions or '')[:200])
print('Loaded examples:', len(examples))
"
```

If this succeeds, the agent uses Supabase-hosted instructions and examples for SAR. **OFAC_REJECT** works without Supabase: if no row exists for that code, local guidance from `report_types.py` is used.

### Documentation

- **`docs/NARRATIVE_VALIDATION.md`** — Narrative quality criteria, validation process, and pass/fail checks.
- **`docs/OFAC_REJECT.md`** — OFAC_REJECT routing, KB retrieval, required input, validation, and example narrative.

### License

Internal use / Capstone project.
