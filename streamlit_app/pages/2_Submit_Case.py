from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from streamlit_app.components.agent_timeline import render_agent_timeline
from streamlit_app.components.header import load_styles, render_header
from streamlit_app.components.report_preview import render_pdf_preview
from streamlit_app.components.sidebar import render_sidebar
from streamlit_app.components.status_badge import status_badge
from streamlit_app.config.settings import settings
from streamlit_app.utils.api_client import APIClient, APIClientError
from streamlit_app.utils.formatting import format_currency
from streamlit_app.utils.session_state import add_tracked_job, init_session_state
from streamlit_app.utils.validators import (
    build_manual_case_payload,
    build_text_case_payload,
    merge_intake_answers,
    validate_transaction_data,
)


AGENT_ORDER = ["router", "aggregator", "narrative", "validator", "filer"]
STAGE_LABELS = {
    "router": "Classification",
    "aggregator": "Data Preparation",
    "narrative": "Narrative Drafting",
    "validator": "Quality Checks",
    "filer": "Report Filing",
}


def _result_payload(details: dict[str, Any]) -> dict[str, Any]:
    payload = details.get("result")
    return payload if isinstance(payload, dict) else {}


def _report_types(details: dict[str, Any]) -> list[str]:
    report_types = details.get("report_types")
    if isinstance(report_types, list):
        return [str(item).upper() for item in report_types]
    router_types = (_result_payload(details).get("router") or {}).get("report_types")
    if isinstance(router_types, list):
        return [str(item).upper() for item in router_types]
    return []


def _narrative_required(result: dict[str, Any], report_types: list[str]) -> bool:
    by_type = result.get("aggregator_by_type")
    if isinstance(by_type, dict):
        sar = by_type.get("SAR")
        if isinstance(sar, dict):
            return bool(sar.get("narrative_required", True))
    return "SAR" in report_types


def _timeline(details: dict[str, Any]) -> list[dict[str, Any]]:
    status = str(details.get("status", "pending")).lower()
    current = str(details.get("current_agent") or "").lower()
    result = _result_payload(details)
    report_types = _report_types(details)
    narrative_required = _narrative_required(result, report_types)
    final_payload = result.get("final") if isinstance(result.get("final"), dict) else {}
    needs_review = str(final_payload.get("status", "")).lower() == "needs_review"
    current_index = AGENT_ORDER.index(current) if current in AGENT_ORDER else -1

    description = {
        "router": (result.get("router") or {}).get("reasoning", "Classifying filing type"),
        "aggregator": "Mapping case fields and risk flags",
        "narrative": "Generating SAR narrative section",
        "validator": ((result.get("validation") or {}).get("status") or "Running validation checks"),
        "filer": (final_payload.get("status") or "Generating PDF output"),
    }

    out: list[dict[str, Any]] = []
    for idx, agent in enumerate(AGENT_ORDER):
        if agent == "narrative" and not narrative_required:
            state = "skipped"
            desc = "Narrative not required for this report type"
        elif status == "completed":
            if agent == "filer" and needs_review:
                state = "skipped"
                desc = "Skipped because validation requires human review"
            else:
                state = "completed"
                desc = description.get(agent, "")
        elif status in {"failed", "error"}:
            if current == agent:
                state = "error"
            elif current_index >= 0 and idx < current_index:
                state = "completed"
            else:
                state = "pending"
            desc = description.get(agent, "")
        else:
            if current == agent:
                state = "active"
            elif current_index >= 0 and idx < current_index:
                state = "completed"
            else:
                state = "pending"
            desc = description.get(agent, "")
        out.append({"name": STAGE_LABELS.get(agent, agent.title()), "status": state, "description": desc})
    return out


def _rows_from_dict(data: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            value_str = json.dumps(value, default=str)
        else:
            value_str = str(value)
        rows.append({"Field": str(key), "Value": value_str})
    return rows


def _submit_payload(api_client: APIClient, payload: dict[str, Any]) -> None:
    try:
        with st.spinner("Submitting case for processing..."):
            result = api_client.submit_case(payload)
        job_id = result.get("job_id")
        if not job_id:
            st.error("Submission succeeded but job_id was not returned.")
            return
        add_tracked_job(job_id)
        st.session_state["selected_job_id"] = job_id
        st.success(f"Case submitted successfully. Job ID: {job_id}")
    except APIClientError as exc:
        st.error(str(exc))


def _render_agent_outputs(details: dict[str, Any], api_client: APIClient) -> None:
    result = _result_payload(details)
    router = result.get("router") if isinstance(result.get("router"), dict) else {}
    by_type = result.get("aggregator_by_type") if isinstance(result.get("aggregator_by_type"), dict) else {}
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    narrative = result.get("narrative") if isinstance(result.get("narrative"), dict) else {}
    final_payload = result.get("final") if isinstance(result.get("final"), dict) else {}

    report_types = _report_types(details)
    primary = "SAR" if "SAR" in report_types else ("CTR" if "CTR" in report_types else "")
    aggregate = by_type.get(primary) if isinstance(by_type.get(primary), dict) else {}
    if not aggregate and isinstance(result.get("aggregator"), dict):
        aggregate = result.get("aggregator")

    st.markdown("#### Classification")
    st.table(
        _rows_from_dict(
            {
                "Report Types": router.get("report_types", []),
                "Reasoning": router.get("reasoning"),
                "Confidence Score": router.get("confidence_score"),
                "Knowledge Base Status": router.get("kb_status"),
            }
        )
    )

    st.markdown("#### Data Preparation")
    st.table(
        _rows_from_dict(
            {
                "Report Type": aggregate.get("report_type"),
                "Case ID": aggregate.get("case_id"),
                "Total Amount Involved": aggregate.get("total_amount_involved"),
                "Risk Score": aggregate.get("risk_score"),
                "Missing Required Fields": aggregate.get("missing_required_fields", []),
                "Narrative Required": aggregate.get("narrative_required"),
            }
        )
    )

    st.markdown("#### Narrative Drafting")
    if narrative.get("narrative_text"):
        st.success("Narrative generated.")
        st.write(narrative.get("narrative_text"))
    else:
        st.info("Narrative step skipped (not required for this report type).")

    st.markdown("#### Quality Checks")
    if validation:
        status_badge(str(validation.get("status", "unknown")).lower().replace(" ", "_"))
        st.write("")
        st.table(
            _rows_from_dict(
                {
                    "Approval Flag": validation.get("approval_flag"),
                    "Status": validation.get("status"),
                    "Completeness Score": validation.get("completeness_score"),
                    "Compliance Checks": validation.get("compliance_checks", {}),
                    "Issues": validation.get("issues", []),
                    "Recommendations": validation.get("recommendations", []),
                }
            )
        )
    else:
        st.info("Validation output is not available yet.")

    st.markdown("#### Report Filing")
    filed_reports: list[dict[str, Any]] = []
    if isinstance(final_payload.get("reports"), list):
        for item in final_payload["reports"]:
            if isinstance(item, dict):
                filed_reports.append(item)
    elif final_payload.get("pdf_path"):
        filed_reports.append(final_payload)

    if filed_reports:
        st.success("Filing completed.")
        rows = []
        for item in filed_reports:
            rows.append(
                {
                    "Report Type": item.get("report_type", ""),
                    "Fields Filled": item.get("fields_filled", ""),
                    "Attempted Fields": item.get("attempted_fields", ""),
                    "Template Fields": item.get("template_field_count", ""),
                    "Template": item.get("template_variant", item.get("template_path", "")),
                    "Generated At": item.get("generated_at", ""),
                }
            )
        st.table(rows)

        for report in filed_reports:
            report_type = str(report.get("report_type") or "").upper()
            if not report_type:
                continue
            button_key = f"submit_page_download_{details.get('job_id')}_{report_type}"
            if st.button(f"Download {report_type} PDF", key=button_key):
                try:
                    pdf_bytes = api_client.download_report(details["job_id"], report_type=report_type)
                    render_pdf_preview(pdf_bytes, f"{report_type}_{details.get('job_id')}.pdf")
                except APIClientError as exc:
                    st.error(str(exc))
    elif str(final_payload.get("status", "")).lower() == "needs_review":
        st.error("Needs human review. Filing skipped.")
        st.markdown("##### Case Summary for Reviewer")
        st.write(f"Case ID: {aggregate.get('case_id', details.get('job_id'))}")
        st.write(f"Report Types: {', '.join(report_types) if report_types else '-'}")
        st.write(f"Total Amount: {format_currency(aggregate.get('total_amount_involved', 0))}")
        issues = validation.get("issues", [])
        if isinstance(issues, list) and issues:
            st.write("Reasons:")
            for issue in issues:
                st.write(f"- {issue}")
    else:
        st.info("Filer output is not available yet.")


def _monitor_job(api_client: APIClient) -> None:
    selected_job = st.session_state.get("selected_job_id")
    if not selected_job:
        return

    st.markdown("---")
    st.markdown(f"### Live Workflow Monitor - {selected_job}")
    auto_refresh = st.toggle("Auto-refresh monitor", value=True, key="submit_monitor_auto_refresh")

    try:
        details = api_client.get_job_status(selected_job)
    except APIClientError as exc:
        st.error(str(exc))
        return

    top1, top2, top3 = st.columns(3)
    with top1:
        st.metric("Job Status", str(details.get("status", "unknown")).upper())
    with top2:
        current_stage_key = str(details.get("current_agent") or "").lower()
        st.metric("Current Stage", STAGE_LABELS.get(current_stage_key, "-"))
    with top3:
        st.metric("Progress", f"{int(details.get('progress') or 0)}%")
    st.progress(int(details.get("progress") or 0))

    tab_timeline, tab_outputs, tab_table = st.tabs(["Workflow Progress", "Case Summary", "Processing Table"])
    with tab_timeline:
        render_agent_timeline(_timeline(details))
    with tab_outputs:
        _render_agent_outputs(details, api_client)
    with tab_table:
        summary_rows = [
            {"Field": "Job ID", "Value": str(details.get("job_id", ""))},
            {"Field": "Status", "Value": str(details.get("status", ""))},
            {"Field": "Current Stage", "Value": STAGE_LABELS.get(str(details.get("current_agent") or "").lower(), "-")},
            {"Field": "Progress", "Value": f"{int(details.get('progress') or 0)}%"},
            {"Field": "Report Types", "Value": ", ".join(_report_types(details))},
        ]
        result = _result_payload(details)
        final_payload = result.get("final") if isinstance(result.get("final"), dict) else {}
        if final_payload:
            summary_rows.append({"Field": "Final Status", "Value": str(final_payload.get("status", ""))})
            summary_rows.append({"Field": "Final Message", "Value": str(final_payload.get("message", ""))})
        st.table(summary_rows)

    if auto_refresh and str(details.get("status", "")).lower() in {"pending", "submitted", "processing"}:
        time.sleep(max(int(st.session_state.get("notifications_refresh_seconds", 5)), 2))
        st.rerun()


st.set_page_config(
    page_title="Submit Case",
    page_icon=settings.page_icon,
    layout=settings.layout,
    initial_sidebar_state="expanded",
)
init_session_state()
load_styles()

if st.session_state.get("api_client") is None:
    st.session_state["api_client"] = APIClient(
        base_url=st.session_state.get("settings_api_url", settings.api_base_url),
        timeout=int(st.session_state.get("settings_timeout", settings.request_timeout_seconds)),
    )
api_client: APIClient = st.session_state["api_client"]

render_sidebar(api_client)
render_header("Submit New Case", "Submit text or structured data for end-to-end compliance processing")

st.markdown(
    """
    <div class="submit-hero">
      <div class="submit-hero-title">Case Intake Workspace</div>
      <div class="submit-hero-subtitle">
        Choose one input method, submit the case, and track processing in real time.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

step_1, step_2, step_3 = st.columns(3)
with step_1:
    st.markdown(
        """
        <div class="submit-step-card">
          <div class="submit-step-title">1. Provide Input</div>
          <div class="submit-step-text">Use text, JSON upload, or manual entry form.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with step_2:
    st.markdown(
        """
        <div class="submit-step-card">
          <div class="submit-step-title">2. Start Processing</div>
          <div class="submit-step-text">Submit the case for classification and preparation.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with step_3:
    st.markdown(
        """
        <div class="submit-step-card">
          <div class="submit-step-title">3. Review Outcome</div>
          <div class="submit-step-text">Track status and download the final filed report.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown('<div class="home-section-title">Case Submission</div>', unsafe_allow_html=True)

t_text, t_upload, t_manual, t_batch, t_direct = st.tabs(
    ["Text Input", "Upload JSON", "Manual Entry", "Batch Upload", "Direct PDF Filing"]
)

with t_text:
    # ── Chat session state ─────────────────────────────────────────────────
    if "chat_messages" not in st.session_state:
        st.session_state["chat_messages"] = []          # [{role, content}]
    if "chat_case" not in st.session_state:
        st.session_state["chat_case"] = {}
    if "chat_missing" not in st.session_state:
        st.session_state["chat_missing"] = []           # remaining fields to collect
    if "chat_answers" not in st.session_state:
        st.session_state["chat_answers"] = {}           # key → answered value
    if "chat_stage" not in st.session_state:
        st.session_state["chat_stage"] = "idle"         # idle | questioning | ready | submitted

    def _chat_reset() -> None:
        st.session_state["chat_messages"] = []
        st.session_state["chat_case"] = {}
        st.session_state["chat_missing"] = []
        st.session_state["chat_answers"] = {}
        st.session_state["chat_stage"] = "idle"

    def _next_question() -> str | None:
        """Return the prompt for the next unanswered required field, or None."""
        for field in st.session_state["chat_missing"]:
            if field["key"] not in st.session_state["chat_answers"]:
                placeholder = field.get("placeholder", "")
                hint = f" (e.g. {placeholder})" if placeholder else ""
                return field["label"] + hint
        return None

    def _assistant(msg: str) -> None:
        st.session_state["chat_messages"].append({"role": "assistant", "content": msg})

    def _user(msg: str) -> None:
        st.session_state["chat_messages"].append({"role": "user", "content": msg})

    # ── Header row ────────────────────────────────────────────────────────
    hcol, rcol = st.columns([5, 1])
    with hcol:
        st.markdown("#### Compliance Chat")
        st.caption("Describe the suspicious activity or paste a case. I'll extract what I can and ask for anything missing.")
    with rcol:
        if st.button("Clear chat", use_container_width=True):
            _chat_reset()
            st.rerun()

    # ── Render conversation history ───────────────────────────────────────
    for msg in st.session_state["chat_messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # ── "ready" stage — show confirm/submit above the input ───────────────
    if st.session_state["chat_stage"] == "ready":
        case = st.session_state["chat_case"]
        sai = case.get("SuspiciousActivityInformation") or {}
        subject_name = (case.get("subject") or {}).get("name") or "Unknown"
        amount = (sai.get("26_AmountInvolved") or {}).get("amount_usd") or 0
        dr = sai.get("27_DateOrDateRange") or {}
        summary_lines = [
            f"**Subject:** {subject_name}",
            f"**Amount:** ${amount:,.2f}" if amount else "**Amount:** not specified",
        ]
        if dr.get("from") and dr.get("to"):
            summary_lines.append(f"**Activity period:** {dr['from']} – {dr['to']}")
        with st.chat_message("assistant"):
            st.markdown("All required information collected. Here's the summary:\n\n" + "\n\n".join(summary_lines))
            st.markdown("Ready to submit — click **Submit Case** to process, or type anything to add more context.")
        if st.button("Submit Case", use_container_width=True, type="primary"):
            st.session_state["chat_stage"] = "submitted"
            _submit_payload(api_client, st.session_state["chat_case"])
            st.rerun()

    # ── Chat input ────────────────────────────────────────────────────────
    user_input = st.chat_input(
        "Describe the case or answer the question above…",
        disabled=(st.session_state["chat_stage"] == "submitted"),
    )

    if user_input:
        _user(user_input)
        stage = st.session_state["chat_stage"]

        if stage == "idle":
            # First message — call backend router/analyze to identify truly missing fields
            partial = build_text_case_payload(user_input)
            with st.spinner("Analyzing case…"):
                analysis = api_client.analyze_case(text=user_input, case_data=partial)

            # Use router-detected missing_field_prompts (only fields not found in input)
            # May be a list of {field_path, prompt} dicts or a {field: prompt} dict
            raw_missing = analysis.get("missing_field_prompts") or []
            if isinstance(raw_missing, dict):
                missing = [{"key": k, "label": v, "required": True} for k, v in raw_missing.items()]
            elif isinstance(raw_missing, list):
                missing = [
                    {
                        "key": str(item.get("input_key") or item.get("field_path") or item.get("key") or i),
                        "label": str(item.get("ask_user_prompt") or item.get("field_label") or item.get("label") or item.get("input_key") or item),
                        "required": True,
                    }
                    for i, item in enumerate(raw_missing)
                    if isinstance(item, dict)
                ]
            else:
                missing = []

            st.session_state["chat_case"] = partial
            st.session_state["chat_missing"] = missing
            st.session_state["chat_answers"] = {}
            # Store report type from router for later submission
            if analysis.get("report_type"):
                partial["report_type_hint"] = analysis["report_type"]
                partial["report_type"] = analysis["report_type"]
                st.session_state["chat_case"] = partial

            # Build extraction summary from router's detected fields
            det = analysis.get("detected") or {}
            detected = []
            if det.get("subject_name") and det["subject_name"] not in ("Subject Unknown", "Unknown Subject", ""):
                detected.append(f"**Subject:** {det['subject_name']}")
            if det.get("amount"):
                detected.append(f"**Amount involved:** ${float(det['amount']):,.2f}")
            if det.get("date_from") and det.get("date_to"):
                detected.append(f"**Activity period:** {det['date_from']} – {det['date_to']}")
            if det.get("activity_types"):
                detected.append(f"**Activity categories:** {', '.join(det['activity_types'])}")
            if analysis.get("report_type"):
                detected.append(f"**Report type:** {analysis['report_type']}")

            if not missing:
                st.session_state["chat_stage"] = "ready"
                reply = ("I've extracted all required information from your input."
                         + ("\n\n" + "\n\n".join(detected) if detected else ""))
            else:
                st.session_state["chat_stage"] = "questioning"
                intro = ("I've extracted the following:\n\n" + "\n\n".join(detected) + "\n\n") if detected else ""
                next_q = _next_question()
                reply = intro + f"To complete the FinCEN filing I need a few more details.\n\n**{next_q}**"
            _assistant(reply)

        elif stage == "questioning":
            # Map this answer to the current open field
            for field in st.session_state["chat_missing"]:
                if field["key"] not in st.session_state["chat_answers"]:
                    st.session_state["chat_answers"][field["key"]] = user_input.strip()
                    break

            # Merge all answers collected so far into the case
            updated_case = merge_intake_answers(
                st.session_state["chat_case"],
                st.session_state["chat_answers"],
            )
            st.session_state["chat_case"] = updated_case

            next_q = _next_question()
            if next_q is None:
                st.session_state["chat_stage"] = "ready"
                _assistant("Got it. I have everything I need.")
            else:
                _assistant(f"Got it. Next: **{next_q}**")

        elif stage == "ready":
            # User typed something extra — treat as additional context in narrative
            case = st.session_state["chat_case"]
            existing_narrative = str(case.get("narrative") or "")
            case["narrative"] = (existing_narrative + "\n\nAdditional context: " + user_input.strip()).strip()
            st.session_state["chat_case"] = case
            _assistant("Added to the case notes. Click **Submit Case** when ready.")

        st.rerun()

with t_upload:
    uploaded = st.file_uploader("Upload case JSON", type=["json"])
    parsed = None
    if uploaded is not None:
        try:
            parsed = json.load(uploaded)
            st.success("File uploaded successfully")
            with st.expander("Preview Data"):
                st.json(parsed)
        except json.JSONDecodeError as exc:
            st.error(f"Invalid JSON: {exc}")

    if parsed is not None:
        candidate = parsed[0] if isinstance(parsed, list) and parsed else parsed
        valid, errors = validate_transaction_data(candidate)
        if valid:
            st.success("Validation passed")
            if st.button("Submit JSON for Analysis", use_container_width=True):
                payload = candidate if isinstance(candidate, dict) else {}
                _submit_payload(api_client, payload)
        else:
            st.error("Validation failed")
            for err in errors:
                st.write(f"- {err}")

with t_manual:
    lcol, rcol = st.columns(2)
    with lcol:
        subject_name = st.text_input("Subject Name *", key="manual_subject_name")
        subject_type = st.selectbox("Subject Type", ["Individual", "Entity"])
        country = st.text_input("Country", value="US")
        occupation = st.text_input("Occupation/Industry")

    with rcol:
        amount = st.number_input("Total Amount USD *", min_value=0.0, value=1000.0, step=100.0)
        from_date = st.date_input("Activity From Date", value=date.today() - timedelta(days=1))
        to_date = st.date_input("Activity To Date", value=date.today())
        instrument = st.selectbox("Instrument Type", ["U.S. Currency", "Wire", "Check", "ACH", "Other"])

    st.markdown("##### Suspicious Activity Indicators")
    c1, c2, c3 = st.columns(3)
    with c1:
        structuring = st.checkbox("Structuring")
        money_laundering = st.checkbox("Money Laundering")
        terrorist = st.checkbox("Terrorist Financing")
    with c2:
        wire_fraud = st.checkbox("Wire Fraud")
        identity = st.checkbox("Identity Theft")
        check_fraud = st.checkbox("Check Fraud")
    with c3:
        takeover = st.checkbox("Account Takeover")
        mortgage = st.checkbox("Mortgage Fraud")
        other = st.checkbox("Other")

    notes = st.text_area("Additional Notes", height=150)

    if st.button("Submit Manual Case", use_container_width=True):
        flags = [
            label
            for enabled, label in [
                (structuring, "Structuring"),
                (money_laundering, "Money Laundering"),
                (terrorist, "Terrorist Financing"),
                (wire_fraud, "Wire Fraud"),
                (identity, "Identity Theft"),
                (check_fraud, "Check Fraud"),
                (takeover, "Account Takeover"),
                (mortgage, "Mortgage Fraud"),
                (other, "Other"),
            ]
            if enabled
        ]

        payload = build_manual_case_payload(
            subject_name=subject_name,
            subject_type=subject_type,
            country=country,
            occupation=occupation,
            amount_usd=amount,
            from_date=from_date,
            to_date=to_date,
            instrument_type=instrument,
            suspicious_flags=flags,
            notes=notes,
        )

        valid, errors = validate_transaction_data(payload)
        if not valid:
            st.error("Validation failed")
            for err in errors:
                st.write(f"- {err}")
        else:
            _submit_payload(api_client, payload)

with t_batch:
    st.info("Batch upload is available in the next release. CSV and ZIP support will be added.")
    st.file_uploader("Upload CSV or ZIP", type=["csv", "zip"])
    with st.expander("CSV Format Guidance"):
        st.code("subject_name,amount_usd,from_date,to_date,instrument_type,flags")


with t_direct:
    st.markdown("#### Direct PDF Filing")
    st.caption("This bypasses agent workflow and calls /api/v1/reports/file-direct.")

    report_type = st.selectbox("Report Type", ["auto", "SAR", "CTR", "BOTH"])
    input_mode = st.radio(
        "Input Source",
        ["Use Existing JSON Path", "Upload JSON File"],
        horizontal=True,
    )

    json_path = ""
    if input_mode == "Use Existing JSON Path":
        json_path = st.text_input(
            "JSON Path",
            value="data/CASE-2024-677021.json",
            help="Path must be readable from the backend process.",
        )
    else:
        direct_file = st.file_uploader(
            "Upload JSON for direct filing",
            type=["json"],
            key="direct_pdf_upload",
        )
        if direct_file is not None:
            temp_dir = PROJECT_ROOT / "data" / "tmp"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_path = temp_dir / f"direct_{direct_file.name}"
            temp_path.write_bytes(direct_file.getvalue())
            json_path = str(temp_path.relative_to(PROJECT_ROOT))
            st.caption(f"Saved temporary input to `{json_path}`")

    if st.button("Run Direct Filing", use_container_width=True):
        if not json_path.strip():
            st.warning("Provide a JSON path or upload a file.")
        else:
            try:
                with st.spinner("Filing PDF directly..."):
                    direct_result = api_client.file_report_direct(
                        json_path=json_path.strip(),
                        report_type=report_type,
                    )
                st.success("Direct filing completed")
                st.json(direct_result)
            except APIClientError as exc:
                st.error(str(exc))

_monitor_job(api_client)
