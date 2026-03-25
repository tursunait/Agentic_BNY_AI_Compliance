import ast
import json
from typing import Any, Callable, Dict

from crewai import Crew, Process, LLM

from backend.tools.kb_tools import (
    search_kb_tool,
    get_validation_rules_tool,
)
from backend.tools.pdf_tools import CTRReportFiler, SARReportFiler
from backend.pdf_filler.agent import PdfFillerAgent
from backend.tools.field_mapper import (
    calculate_total_cash_amount,
    determine_report_types,
    has_suspicious_activity,
    normalize_case_data,
)
from backend.config.settings import settings
from backend.agents.aggregator_agent import AggregatorOrchestrator
from backend.agents.router_agent import (
    create_router_agent,
    create_router_task,
    derive_and_normalize_case,
    strip_prompt_keys,
    fallback_classify,
    run_router,
)
from backend.agents.narrative_agent import generate_narrative_payload
from backend.agents.validator_agent import create_validator_agent, create_validator_task


def _parse_raw_user_input(raw: Any) -> Dict[str, Any]:
    """Try json.loads then ast.literal_eval on a raw_user_input string."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    text = raw.strip()
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    # Extract just the dict portion (first { to last }) before ast.literal_eval
    # so trailing prose or formatted text after the closing brace doesn't break parsing.
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    candidates = [text]
    if first_brace != -1 and last_brace > first_brace:
        extracted = text[first_brace : last_brace + 1]
        if extracted != text:
            candidates.insert(0, extracted)
    for candidate in candidates:
        try:
            result = ast.literal_eval(candidate)
            if isinstance(result, dict):
                return result
        except Exception:
            pass
    return {}


def _extract_structured_fields_from_narrative(
    narrative_text: str,
    report_type: str,
) -> Dict[str, Any]:
    """
    Use the LLM to extract structured field values from free-text narrative.
    Field definitions are pulled from the Supabase schema for the given report type.
    Returns a flat dict of {field_key: value} using dot-notation keys where nested.
    """
    from backend.knowledge_base.supabase_client import SupabaseClient
    import requests as _requests

    api_key = str(getattr(settings, "OPENAI_API_KEY", "") or "").strip()
    if not api_key:
        return {}

    try:
        db = SupabaseClient()
        schema = db.get_schema(report_type) or {}
    except Exception:
        schema = {}

    required_paths = schema.get("required_fields") or schema.get("requiredFields") or []
    if not required_paths:
        return {}

    system_prompt = (
        "You extract structured data from a compliance officer's narrative. "
        "Return a JSON object whose keys are exactly the field paths listed. "
        "Set each value to what the narrative states, or null if not mentioned. "
        "Preserve dot-notation keys as-is (e.g. subject.first_name). "
        "For dates use MM/DD/YYYY. For amounts use numbers without currency symbols."
    )
    user_content = json.dumps(
        {
            "report_type": report_type,
            "narrative": narrative_text[:12000],
            "required_field_paths": required_paths,
        },
        ensure_ascii=False,
    )

    model = str(getattr(settings, "OPENAI_MODEL", "") or "gpt-4o-mini").strip()
    base_url = str(getattr(settings, "OPENAI_BASE_URL", "") or "https://api.openai.com/v1").rstrip("/")

    try:
        resp = _requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = (((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        parsed = json.loads(content) if content else {}
        if not isinstance(parsed, dict):
            return {}
        return {k: v for k, v in parsed.items() if v is not None and str(v).strip()}
    except Exception as exc:
        from loguru import logger as _logger
        _logger.warning("LLM narrative extraction failed: {}", exc)
        return {}


def _merge_extracted_into_case(case_data: Dict[str, Any], extracted: Dict[str, Any]) -> None:
    """Merge LLM-extracted flat/dot-path key-value pairs into case_data."""
    for input_key, value in extracted.items():
        if not isinstance(input_key, str) or not input_key.strip():
            continue
        parts = input_key.strip().split(".")
        node = case_data
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value


def _enrich_from_raw_user_input(normalized: Dict[str, Any]) -> Dict[str, Any]:
    """
    If source_type is free_text, parse raw_user_input and merge the rich
    structured fields back, overwriting the synthetic placeholders.
    Priority fields: case_id, subject, institution, transactions,
    SuspiciousActivityInformation (especially amount and date range).
    When raw_user_input is not JSON-parseable, use LLM extraction to pull
    structured fields from the narrative text.
    """
    if normalized.get("source_type") != "free_text":
        return normalized
    raw = normalized.get("raw_user_input")
    if not raw:
        return normalized

    parsed = _parse_raw_user_input(raw)
    if not parsed:
        # True free-text narrative: extract structured fields via LLM
        report_type = str(normalized.get("report_type_hint") or normalized.get("report_type") or "SAR").strip().upper()
        extracted = _extract_structured_fields_from_narrative(str(raw), report_type)
        if extracted:
            enriched = dict(normalized)
            _merge_extracted_into_case(enriched, extracted)
            return enriched
        return normalized

    enriched = dict(normalized)

    # Merge top-level identity fields from parsed source
    for key in ("case_id", "subject", "institution", "transactions", "narrative", "alert", "external_signals", "data_quality"):
        val = parsed.get(key)
        if val and (not enriched.get(key) or str(enriched.get(key, "")).startswith("CASE-TEXT") or enriched.get(key) in ("Unknown Subject", "SUB-UNKNOWN", "UNKNOWN")):
            enriched[key] = val

    # Always take subject from parsed if it has a real name
    parsed_subject = parsed.get("subject") or {}
    if isinstance(parsed_subject, dict) and parsed_subject.get("name") not in (None, "Unknown Subject", ""):
        enriched["subject"] = parsed_subject

    # Merge SuspiciousActivityInformation — prioritise parsed amount and date range
    parsed_sai = parsed.get("SuspiciousActivityInformation") or {}
    current_sai = enriched.get("SuspiciousActivityInformation") or {}
    if isinstance(parsed_sai, dict) and parsed_sai:
        merged_sai = dict(current_sai)
        for field in ("26_AmountInvolved", "27_DateOrDateRange", "28_CumulativeAmount",
                      "29_Structuring", "33_MoneyLaundering", "35_OtherSuspiciousActivities",
                      "39_ProductTypesInvolved", "40_InstrumentTypesInvolved"):
            if parsed_sai.get(field):
                merged_sai[field] = parsed_sai[field]
        enriched["SuspiciousActivityInformation"] = merged_sai

    # Replace synthetic single-transaction placeholder with real transactions
    parsed_txns = parsed.get("transactions") or []
    current_txns = enriched.get("transactions") or []
    synthetic = len(current_txns) == 1 and (current_txns[0] or {}).get("tx_id", "").startswith("text-")
    if parsed_txns and synthetic:
        enriched["transactions"] = parsed_txns

    return enriched


def _strip_fences(text: str) -> str:
    """Strip markdown code fences from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence (```json, ```JSON, ``` etc.)
        text = text[text.index("\n") + 1:] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[: text.rfind("```")].rstrip()
    return text.strip()


def _parse_jsonish(payload) -> Dict:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        cleaned = _strip_fences(payload)
        try:
            result = json.loads(cleaned)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
        return {}
    raw = getattr(payload, "raw", None)
    if isinstance(raw, str):
        cleaned = _strip_fences(raw)
        try:
            result = json.loads(cleaned)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
        return {}
    return {}


def _build_router_reasoning(total_cash_amount: float, suspicious: bool, report_types: list[str]) -> str:
    if not report_types:
        return "No suspicious indicators and cash amount below filing threshold."
    reasons = []
    if "CTR" in report_types:
        reasons.append(f"total cash amount is ${total_cash_amount:,.2f} (>= $10,000)")
    if "SAR" in report_types and suspicious:
        reasons.append("suspicious activity indicators are present")
    if len(report_types) == 2:
        return "Both report types required because " + " and ".join(reasons) + "."
    if report_types[0] == "CTR":
        return "CTR required because " + " and ".join(reasons) + "."
    return "SAR required because " + " and ".join(reasons) + "."


def _build_narrative_input(normalized_case: Dict[str, Any], sar_aggregate: Dict[str, Any]) -> Dict[str, Any]:
    """Build Agent 4 input payload with required keys."""
    output = dict(normalized_case)
    output["case_id"] = sar_aggregate.get("case_id") or output.get("case_id")

    subject = output.get("subject")
    if not isinstance(subject, dict) or not subject:
        subject = {
            "subject_id": sar_aggregate.get("customer_id"),
            "name": sar_aggregate.get("customer_name"),
        }
    output["subject"] = subject

    suspicious_info = output.get("SuspiciousActivityInformation")
    if not isinstance(suspicious_info, dict) or not suspicious_info:
        suspicious_info = sar_aggregate.get("SuspiciousActivityInformation")
    if not isinstance(suspicious_info, dict):
        suspicious_info = {
            "26_AmountInvolved": {"amount_usd": sar_aggregate.get("total_amount_involved", 0.0), "no_amount": False},
            "27_DateOrDateRange": {
                "from": (sar_aggregate.get("activity_date_range") or {}).get("start"),
                "to": (sar_aggregate.get("activity_date_range") or {}).get("end"),
            },
            "35_OtherSuspiciousActivities": sar_aggregate.get("suspicious_activity_type", []),
        }
    output["SuspiciousActivityInformation"] = suspicious_info
    return output


def _normalize_report_types(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
            raw = parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            raw = []
    else:
        raw = []

    out: list[str] = []
    for item in raw:
        report_type = str(item or "").strip().upper()
        if report_type in {"SAR", "CTR", "OFAC_REJECT"} and report_type not in out:
            out.append(report_type)
    return out


def _is_ofac_case(case: Dict[str, Any]) -> bool:
    """Return True when the case payload is an OFAC rejection report."""
    if str(case.get("report_type_code", "")).upper() == "OFAC_REJECT":
        return True

    # Accept both 'transaction' (legacy) and 'transaction_information' (OFAC form layout)
    txn = case.get("transaction") or case.get("transaction_information")
    if isinstance(txn, dict) and txn.get("date_of_rejection"):
        # Legacy: paired with case_facts / sanctions_program
        if case.get("case_facts") or case.get("sanctions_program"):
            return True
        # OFAC form layout: rejection reason field often contains "OFAC" or "SDN"
        reason = str(txn.get("program_or_reason_for_rejecting_funds", "")).upper()
        if "OFAC" in reason or "SDN" in reason or "SANCTION" in reason:
            return True
        # Has originator + beneficiary_financial_institution → wire rejection pattern
        if case.get("originator") and case.get("beneficiary_financial_institution"):
            return True

    return False


def _build_ofac_aggregator_output(case: Dict[str, Any]) -> Dict[str, Any]:
    """Build a minimal aggregator-style output for OFAC rejection cases.

    Handles two payload shapes:
    - Legacy: keys transaction / institution / case_facts / preparer / sanctions_program
    - OFAC form layout: keys transaction_information / institution_information /
      originator / beneficiary_financial_institution / preparer_information
    """
    # transaction block — accept both key names
    txn = case.get("transaction") or case.get("transaction_information") or {}
    # institution block — accept both key names
    inst = case.get("institution") or case.get("institution_information") or {}
    facts = case.get("case_facts") or {}
    # preparer block — accept both key names
    preparer = case.get("preparer") or case.get("preparer_information") or {}

    # Sanctions program — may live at top level or inside the rejection reason
    sanctions_program = (
        case.get("sanctions_program")
        or txn.get("sanctions_program")
        or txn.get("program_or_reason_for_rejecting_funds")
        or ""
    )

    # Beneficiary FI — legacy field or dedicated key
    beneficiary_fi_obj = case.get("beneficiary_financial_institution") or {}
    beneficiary_fi = (
        txn.get("beneficiary_fi")
        or (beneficiary_fi_obj.get("name") if isinstance(beneficiary_fi_obj, dict) else "")
        or ""
    )

    # Originator
    originator_obj = case.get("originator") or {}
    originator_name = originator_obj.get("name", "") if isinstance(originator_obj, dict) else ""

    # Amount rejected — may be keyed differently
    amount_rejected = txn.get("amount_rejected") or txn.get("amount_rejected_usd") or ""

    # Institution name
    inst_name = inst.get("name") or inst.get("institution") or ""

    # Preparer
    preparer_name = preparer.get("name") or preparer.get("name_of_signer") or ""
    preparer_title = preparer.get("title") or preparer.get("title_of_signer") or ""

    return {
        "report_type": "OFAC_REJECT",
        "case_id": case.get("case_id", "UNKNOWN"),
        "narrative_required": True,
        "narrative_justification": "OFAC rejection reports require a narrative per 31 C.F.R. Part 501",
        "missing_required_fields": [],
        "data_quality_issues": [],
        "risk_score": 1.0,
        "risk_flags": [],
        "institution_name": inst_name,
        "amount_rejected": amount_rejected,
        "currency": txn.get("currency", "USD"),
        "sanctions_program": sanctions_program,
        "transaction_type": txn.get("transaction_type", ""),
        "date_of_rejection": txn.get("date_of_rejection", ""),
        "beneficiary_fi": beneficiary_fi,
        "originator_name": originator_name,
        "disposition": facts.get("disposition", ""),
        "documents_reviewed": facts.get("documents_reviewed", []),
        "preparer_name": preparer_name,
        "preparer_title": preparer_title,
    }


def _build_ofac_pdf_payload(case: Dict[str, Any], aggregator_output: Dict[str, Any]) -> Dict[str, Any]:
    """Build transaction_json with nested keys matching the OFAC_REJECT Supabase PDF mapping.

    The fill_engine resolves dot-paths like 'institution.name' as data['institution']['name'].
    The raw OFAC case uses 'institution_information', 'transaction_information', etc., so we
    remap everything here into the expected shape.
    """
    inst = case.get("institution_information") or case.get("institution") or {}
    txn = case.get("transaction_information") or case.get("transaction") or {}
    prep = case.get("preparer_information") or case.get("preparer") or {}
    originator = case.get("originator") or {}
    originating_fi = case.get("originating_financial_institution") or {}
    intermediary_fis = case.get("intermediary_financial_institutions") or []
    bene_fi_obj = case.get("beneficiary_financial_institution") or {}
    beneficiary = case.get("beneficiary") or {}

    def _name_addr(obj: Any) -> str:
        if not isinstance(obj, dict):
            return ""
        name = obj.get("name", "")
        addr = obj.get("address", "")
        return f"{name}\n{addr}".strip() if addr else name

    intermediary_parts = [
        _name_addr(fi) for fi in (intermediary_fis if isinstance(intermediary_fis, list) else [])
        if isinstance(fi, dict) and fi.get("name")
    ]

    return {
        "institution": {
            "name": inst.get("institution") or inst.get("name") or aggregator_output.get("institution_name", ""),
            "type": inst.get("type_of_institution") or inst.get("type", ""),
            "address": inst.get("address", ""),
            "city": inst.get("city", ""),
            "state": inst.get("state", ""),
            "postal_code": inst.get("postal_code", ""),
            "country": inst.get("country", ""),
            "contact_person": inst.get("contact_person", ""),
            "telephone": inst.get("telephone_number") or inst.get("telephone", ""),
            "email": inst.get("email_address") or inst.get("email", ""),
            "fax": inst.get("fax_number") or inst.get("fax", ""),
        },
        "transaction": {
            "amount_rejected": str(txn.get("amount_rejected") or txn.get("amount_rejected_usd") or aggregator_output.get("amount_rejected", "")),
            "date_of_transaction": txn.get("date_of_transaction", ""),
            "date_of_rejection": txn.get("date_of_rejection") or aggregator_output.get("date_of_rejection", ""),
            "program_or_reason": txn.get("program_or_reason_for_rejecting_funds") or aggregator_output.get("sanctions_program", ""),
            "originator_name_address": _name_addr(originator) or aggregator_output.get("originator_name", ""),
            "originating_fi": _name_addr(originating_fi),
            "intermediary_fi": "\n".join(intermediary_parts),
            "beneficiary_fi": _name_addr(bene_fi_obj) or aggregator_output.get("beneficiary_fi", ""),
            "beneficiary_name_address": _name_addr(beneficiary),
            "additional_relevant_info": case.get("additional_relevant_information", ""),
            "additional_data": case.get("additional_data_in_payment_message", ""),
        },
        "preparer": {
            "name": prep.get("name_of_signer") or prep.get("name") or aggregator_output.get("preparer_name", ""),
            "title": prep.get("title_of_signer") or prep.get("title") or aggregator_output.get("preparer_title", ""),
            "date_prepared": prep.get("date_prepared", ""),
        },
    }


def create_compliance_crew(
    transaction_data: dict,
    on_stage: Callable[[str, int], None] | None = None,
) -> Dict[str, dict]:
    normalized_case = normalize_case_data(transaction_data)
    normalized_case = _enrich_from_raw_user_input(normalized_case)
    # Derive report type hint early (needed by derive_and_normalize_case for CTR person_a fields)
    _early_rt = str(
        normalized_case.get("report_type_hint")
        or normalized_case.get("report_type")
        or normalized_case.get("report_type_code")
        or "SAR"
    ).strip().upper()
    normalized_case = derive_and_normalize_case(normalized_case, _early_rt)
    base_llm = LLM(model="gpt-4o", temperature=0.1, max_tokens=4000, api_key=settings.OPENAI_API_KEY)

    def mark_stage(agent: str, progress: int) -> None:
        if on_stage is None:
            return
        try:
            on_stage(agent, progress)
        except Exception:
            pass

    # --- OFAC detection (deterministic, no LLM needed) ---
    is_ofac = _is_ofac_case(normalized_case)

    router_output: Dict[str, Any] = {}
    if is_ofac:
        mark_stage("router", 15)
        router_output = {
            "report_types": ["OFAC_REJECT"],
            "report_type": "OFAC_REJECT",
            "confidence_score": 1.0,
            "reasoning": "OFAC sanctions rejection case detected from input fields (report_type_code or date_of_rejection + case_facts).",
            "kb_status": "EXISTS",
        }
    else:
        try:
            mark_stage("router", 15)
            router_result = run_router(normalized_case)
            normalized_case = router_result.validated_input
            router_output = router_result.to_dict()
        except Exception as exc:
            router_output = fallback_classify(normalized_case)
            router_output["router_error"] = str(exc)

    total_cash_amount = calculate_total_cash_amount(normalized_case)
    suspicious = has_suspicious_activity(normalized_case)
    if is_ofac:
        report_types = ["OFAC_REJECT"]
    else:
        report_types = _normalize_report_types(router_output.get("report_types"))
        if not report_types:
            report_types = determine_report_types(normalized_case)
        # If the Router identified OFAC_REJECT but deterministic detection missed it,
        # promote is_ofac now so the correct OFAC branch runs downstream.
        if "OFAC_REJECT" in report_types:
            is_ofac = True
            report_types = ["OFAC_REJECT"]
    router_output["report_types"] = report_types
    router_output["total_cash_amount"] = total_cash_amount
    if not is_ofac:
        router_output["reasoning"] = _build_router_reasoning(total_cash_amount, suspicious, report_types)
    router_output.setdefault("confidence_score", 1.0 if report_types else 0.0)
    router_output.setdefault("kb_status", "EXISTS")
    if report_types:
        router_output["report_type"] = "OFAC_REJECT" if is_ofac else ("SAR" if "SAR" in report_types else report_types[0])
    else:
        router_output["report_type"] = "NONE"

    if not report_types:
        return {
            "router": router_output,
            "validation": {
                "approval_flag": False,
                "status": "NO_FILING_REQUIRED",
                "message": "No CTR or SAR requirement detected for this case.",
            },
            "final": {
                "status": "no_filing_required",
                "message": "No CTR or SAR filing requirements met",
            },
        }

    # Researcher (Agent 2) intentionally skipped per workflow requirement.

    # Strip prompt-text artifacts from top-level keys before sending to aggregator
    normalized_case = strip_prompt_keys(normalized_case)

    aggregated_by_type: Dict[str, Dict[str, Any]] = {}
    mark_stage("aggregator", 35)
    if is_ofac:
        aggregated_by_type["OFAC_REJECT"] = _build_ofac_aggregator_output(normalized_case)
    else:
        aggregator = AggregatorOrchestrator(llm=base_llm)
        for report_type in report_types:
            aggregated = aggregator.process(
                raw_data=normalized_case,
                report_type=report_type,
                case_id=normalized_case.get("case_id") if isinstance(normalized_case, dict) else None,
            )
            aggregated_by_type[report_type] = aggregated.model_dump(mode="json")

    primary_report_type = "OFAC_REJECT" if is_ofac else ("SAR" if "SAR" in aggregated_by_type else report_types[0])
    aggregator_output: Dict[str, Any] = aggregated_by_type[primary_report_type]

    narrative_output: Dict[str, Any] = {}
    if is_ofac:
        mark_stage("narrative", 55)
        narrative_output = generate_narrative_payload(
            normalized_case,
            report_type_code="OFAC_REJECT",
            verbose=True,
        )
    else:
        sar_aggregate = aggregated_by_type.get("SAR")
        if isinstance(sar_aggregate, dict) and sar_aggregate.get("narrative_required", True):
            mark_stage("narrative", 55)
            narrative_input = _build_narrative_input(normalized_case, sar_aggregate)
            narrative_output = generate_narrative_payload(
                narrative_input,
                report_type_code="SAR",
                verbose=True,
            )

    mark_stage("validator", 75)
    if settings.SKIP_VALIDATOR_FOR_TESTING:
        validation_output = {
            "status": "APPROVED",
            "approval_flag": True,
            "compliance_checks": {"validator": "SKIPPED_FOR_TESTING"},
            "issues": [],
            "recommendations": ["Validator was bypassed for testing mode."],
            "skip_reason": "SKIP_VALIDATOR_FOR_TESTING=true",
        }
    else:
        validator_agent = create_validator_agent(llm=base_llm, tools=[get_validation_rules_tool, search_kb_tool])
        validator_task = create_validator_task(validator_agent, aggregator_output, narrative_output)
        validator_crew = Crew(
            agents=[validator_agent],
            tasks=[validator_task],
            process=Process.sequential,
            verbose=True,
        )
        validator_result = validator_crew.kickoff()
        validation_output = _parse_jsonish(validator_result)
        if "approval_flag" not in validation_output:
            status = str(validation_output.get("status", "")).upper()
            validation_output["approval_flag"] = status == "APPROVED"
        if "status" not in validation_output:
            validation_output["status"] = "APPROVED" if validation_output.get("approval_flag") else "NEEDS_REVIEW"

    final_output: Dict[str, dict]
    if validation_output.get("approval_flag"):
        # Deterministic filing avoids LLM-output parsing risk for final artifacts.
        mark_stage("filer", 90)
        reports = []
        narrative_text = (
            narrative_output.get("narrative_text")
            or narrative_output.get("narrative")
            or narrative_output.get("text")
        )
        if is_ofac:
            # Build a payload with nested keys matching the Supabase OFAC_REJECT PDF mapping
            # (fill_engine resolves 'institution.name' as data['institution']['name']).
            ofac_payload = _build_ofac_pdf_payload(normalized_case, aggregator_output)
            result = PdfFillerAgent().fill_report(
                report_type_name="OFAC_REJECT",
                transaction_json=ofac_payload,
                narrative_text=narrative_text,
            )
            reports.append({**result.to_dict(), "report_type": "OFAC_REJECT"})
        else:
            if "CTR" in report_types:
                reports.append(CTRReportFiler().fill_from_dict(normalized_case))
            if "SAR" in report_types:
                sar_case = dict(normalized_case)
                if narrative_text:
                    sar_case["narrative"] = narrative_text
                reports.append(SARReportFiler().fill_from_dict(sar_case))
        final_output = reports[0] if len(reports) == 1 else {"status": "success", "reports": reports}
    else:
        final_output = {
            "status": "needs_review",
            "validation_report": validation_output,
            "message": "Report did not pass validation - human review required",
        }

    return {
        "router": router_output,
        "aggregator": aggregator_output,
        "aggregator_by_type": aggregated_by_type,
        "narrative": narrative_output,
        "validation": validation_output,
        "final": final_output,
    }
