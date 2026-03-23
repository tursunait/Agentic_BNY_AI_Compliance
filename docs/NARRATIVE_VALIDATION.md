# Narrative Quality Validation

This document describes how to assess whether a **generated SAR narrative** is valid and meets quality criteria before it is used in a Suspicious Activity Report.

---

## 1. Overview

The narrative is the mandatory free-text section of a SAR that describes the suspicious activity. It must be:

- **Factually accurate** — only information from the input case data may appear.
- **Structurally sound** — single coherent paragraph, suitable length, logical flow.
- **Compliant** — factual and neutral tone, no legal conclusions, aligned with FinCEN and institutional guidelines.

Validation can be **automated** (programmatic checks) and **manual** (human review). This document defines criteria for both.

---

## 2. Validation Criteria

### 2.1 Factual Accuracy (No Hallucination)

| Criterion | Description | Check type |
|-----------|-------------|------------|
| **No invented entities** | Names, account numbers, dates, amounts, and locations must appear in the input data or be generic (e.g., "the subject"). | Automated + Manual |
| **No invented events** | Every stated event (deposit, transfer, alert, etc.) must be supported by the input. | Manual / LLM-assisted |
| **Numbers match** | Stated amounts, dates, and counts must match `SuspiciousActivityInformation`, `transactions`, and related fields. | Automated |
| **Subject and alert alignment** | Subject name, alert ID, red flags, and trigger reasons must match the input. | Automated |

**Pass condition:** Narrative does not introduce facts absent from the input; any quantitative claim can be traced to the input.

---

### 2.2 Structure and Format

| Criterion | Description | Check type |
|-----------|-------------|------------|
| **Single paragraph** | Output is one continuous narrative paragraph (or a small number of logically connected paragraphs if policy allows). | Automated |
| **Length** | Narrative is not empty and not excessively long (e.g., typical range 150–800 words for a SAR narrative). | Automated |
| **No JSON/markdown in text** | Narrative is plain prose; no raw JSON, code blocks, or markdown in the body. | Automated |
| **Complete sentences** | Text is composed of full sentences, not bullet points or fragments. | Automated / Manual |

**Pass condition:** Format matches the expected SAR narrative style and length.

---

### 2.3 Tone and Compliance

| Criterion | Description | Check type |
|-----------|-------------|------------|
| **Factual and objective** | Language describes observed activity and patterns; does not conclude that a crime occurred. | Manual / LLM-assisted |
| **Neutral phrasing** | Uses phrases like "appears inconsistent," "raises concern," "may indicate" rather than accusatory or emotional language. | Automated (forbidden phrase list) + Manual |
| **No legal conclusions** | Does not state that the subject "committed" or "is guilty of" a specific crime. | Automated + Manual |
| **No speculation** | Avoids unsupported speculation (e.g., "the subject likely intended to...") unless clearly tied to input. | Manual |

**Pass condition:** Tone is suitable for regulatory submission and internal review.

---

### 2.4 Content Completeness (SAR Best Practices)

| Criterion | Description | Check type |
|-----------|-------------|------------|
| **Subject identified** | Narrative identifies or clearly refers to the subject (e.g., by name or "the subject"). | Automated |
| **Time frame** | Date or date range of the activity is stated. | Automated |
| **Suspicious patterns** | Red flags / suspicious activity types from the input are reflected (e.g., structuring, rapid movement of funds). | Automated / Manual |
| **Transactional detail** | Key amounts, transaction counts, or product types from the input are included where relevant. | Manual |
| **Institutional actions (if any)** | If the input indicates CTR filing, account closure, law enforcement contact, or ongoing investigation, these are noted. | Manual |

**Pass condition:** Narrative would allow a reviewer to understand who, when, what, and why the SAR is filed.

---

### 2.5 Consistency with Input Schema

| Criterion | Description | Check type |
|-----------|-------------|------------|
| **Report type** | If the report type (e.g., Initial vs. Continuing) is in the input, the narrative framing is consistent. | Manual |
| **SuspiciousActivityInformation** | Narrative aligns with the categories and descriptions in `SuspiciousActivityInformation` (e.g., structuring, money laundering, fraud). | Automated / Manual |

**Pass condition:** No contradiction between narrative and structured input fields.

---

## 3. Validation Process

### 3.1 Recommended Flow

1. **Generate** — Run the narrative agent on the case input to produce `narrative`.
2. **Automated validation** — Run programmatic checks (length, format, forbidden phrases, presence of subject/dates, key facts present in input).
3. **Results** — Collect pass/fail and optional scores per criterion.
4. **Manual review** — For production, a human reviews narratives that failed any criterion or that are high-risk cases.
5. **Sign-off** — Narrative is approved for inclusion in the SAR only after it passes validation and review policy.

### 3.2 Automated Checks (Implementation)

The module `narrative_agent.narrative_validation` provides:

- **Structure checks:** non-empty, single-block text, length within bounds, no raw JSON in body.
- **Forbidden language:** list of phrases that must not appear (e.g., "guilty," "committed," "definitely").
- **Required elements:** narrative mentions subject (from input), date or date range (from input), and key red flags (from input).
- **Factual grounding (optional):** ensure amounts/dates mentioned in the narrative appear in the input (e.g., via regex or NER).

If a check fails, the validation result includes the failing criterion and an optional message.

### 3.3 Manual / LLM-Assisted Checks

- **Hallucination:** Reviewer (or an LLM-as-judge) compares narrative sentences to the input and flags any claim not supported by the input.
- **Tone and compliance:** Reviewer confirms factual, neutral tone and no legal conclusions.
- **Completeness:** Reviewer confirms that subject, time frame, patterns, and material actions are adequately covered.

These can be documented in a checklist and, where appropriate, supported by an LLM that scores narrative vs. input alignment.

---

## 4. Pass/Fail and Remediation

- **Pass:** All automated criteria pass and (if applicable) manual review is satisfied. The narrative can proceed to SAR assembly.
- **Fail:** One or more criteria fail. The narrative should be:
  - **Regenerated** (e.g., with refined prompt or different parameters), or
  - **Edited** by a human to fix factual or tone issues, then re-validated.

Policies may define:
- Which criteria are blocking vs. advisory.
- Whether a single failure blocks submission.
- Who may override a failure (e.g., compliance officer) and under what conditions.

---

## 5. Summary Table

| Category | Key criteria | Automated? |
|----------|----------------|------------|
| Factual accuracy | No invented facts; numbers match input | Partially |
| Structure | Single paragraph, reasonable length, no JSON/markdown | Yes |
| Tone | Neutral, no legal conclusions, no forbidden phrases | Partially |
| Completeness | Subject, dates, red flags, key details | Partially |
| Consistency | Aligned with SuspiciousActivityInformation and report type | Partially |

Using both automated and manual checks ensures that the generated narrative is valid and ready for use in the SAR workflow.
