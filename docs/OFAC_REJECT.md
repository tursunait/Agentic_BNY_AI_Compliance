# OFAC Sanctions Rejected Transaction Report (OFAC_REJECT)

The narrative generator supports the **OFAC_REJECT** report type for sanctions rejected transaction reports. It uses the same architecture as SAR: **detect report type** → **retrieve KB guidance** → **generate narrative** → **validate**.

---

## Routing

- **Entry point:** `generate_narrative(input_data, report_type_code=None)`.
- If `report_type_code` is not passed, it is read from `input_data["report_type_code"]` or `input_data["report_type"]` (default `"SAR"`).
- So sending a payload with `"report_type_code": "OFAC_REJECT"` routes the request to OFAC retrieval + generation without any separate API.

---

## KB retrieval for OFAC_REJECT

- **Primary:** Supabase tables `report_types` and `narrative_examples` filtered by `report_type_code = 'OFAC_REJECT'`. If your KB has a row for OFAC_REJECT (with `narrative_required = TRUE` and `narrative_instructions` / examples), that content is used.
- **Fallback:** If the KB has no row for OFAC_REJECT (or Supabase is unavailable), the agent uses **local guidance** from the report type registry (`narrative_agent.report_types.OFAC_REJECT_SPEC`): instructions (required elements, things to avoid, tone) and one example narrative. So OFAC_REJECT works even without Supabase.

The final LLM prompt always includes:
1. **Retrieved or local instructions** (required elements, avoid rejection/blocking confusion, no speculation).
2. **Retrieved or local examples** (one sanctions rejected wire example).
3. **Structured JSON input** (transaction, case_facts, institution, etc.).

---

## Required input shape

- `case_id`
- `transaction` (e.g. amount_rejected, currency, date_of_transaction, beneficiary_fi, program_or_reason)
- `case_facts` (e.g. alert_trigger, sanctions_nexus, documents_reviewed, disposition)

Optional: `report_type_code: "OFAC_REJECT"`, `institution`, `preparer`, `scenario_type`, `sanctions_program`.

---

## Example generated narrative

For the sample input in `examples/ofac_reject_example.json`, a suitable generated narrative is:

---

On March 6, 2026, First National Bank rejected an outbound wire transfer in the amount of 18,450.00 USD from ABC Import Services (4210 Industrial Blvd, Houston, TX) destined for Tehran Industrial Supply Co. (Tehran, Iran), with beneficiary financial institution Bank Melli Iran. The transaction was flagged by automated screening due to the beneficiary country (Iran). Bank Melli Iran is a sanctioned entity under the Iran Transactions and Sanctions Regulations (ITSR), 31 C.F.R. Part 560. The payment reference indicated settlement for industrial components (invoice INV-2026-0112). The institution reviewed the payment message, invoice INV-2026-0112, customer account records, and sanctions screening output. Based on the sanctions nexus, the transaction was rejected and was not processed. This report is prepared for record-keeping and regulatory compliance.

---

## Validation (OFAC_REJECT)

- **rejection_stated:** Narrative clearly states the transaction was rejected / not processed.
- **rejection_not_blocking:** Narrative does not say the transaction was "blocked" (avoids confusion with blocking/freeze).
- **sanctions_nexus_mentioned:** Narrative describes the sanctions nexus (e.g. Iran, ITSR, sanctioned entity).
- **documents_reviewed_mentioned:** Narrative mentions documents or materials reviewed.
- Standard structure checks: non-empty, word count, no forbidden phrases, no raw JSON in body.

See `validate_narrative(narrative, input_data, report_type_code="OFAC_REJECT")` and `docs/NARRATIVE_VALIDATION.md`.
