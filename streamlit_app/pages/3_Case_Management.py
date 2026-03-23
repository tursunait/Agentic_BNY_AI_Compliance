from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from streamlit_app.components.agent_timeline import render_agent_timeline
from streamlit_app.components.case_card import render_case_card
from streamlit_app.components.header import load_styles, render_header
from streamlit_app.components.sidebar import render_sidebar
from streamlit_app.components.status_badge import status_badge
from streamlit_app.config.settings import settings
from streamlit_app.utils.api_client import APIClient, APIClientError
from streamlit_app.utils.formatting import format_currency, format_datetime
from streamlit_app.utils.session_state import init_session_state


AGENT_ORDER = ["router", "aggregator", "narrative", "validator", "filer"]


def _result_payload(case_or_status: dict[str, Any]) -> dict[str, Any]:
    result = case_or_status.get("result")
    return result if isinstance(result, dict) else {}


def _report_types(case_or_status: dict[str, Any]) -> list[str]:
    report_types = case_or_status.get("report_types")
    if isinstance(report_types, list):
        return [str(item).upper() for item in report_types]
    router_types = (_result_payload(case_or_status).get("router") or {}).get("report_types")
    if isinstance(router_types, list):
        return [str(item).upper() for item in router_types]
    return []


def _aggregated_by_type(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("aggregator_by_type")
    return data if isinstance(data, dict) else {}


def _primary_aggregate(result: dict[str, Any]) -> dict[str, Any]:
    by_type = _aggregated_by_type(result)
    if isinstance(by_type.get("SAR"), dict):
        return by_type["SAR"]
    if isinstance(by_type.get("CTR"), dict):
        return by_type["CTR"]
    agg = result.get("aggregator")
    return agg if isinstance(agg, dict) else {}


def _narrative_required(result: dict[str, Any], report_types: list[str]) -> bool:
    by_type = _aggregated_by_type(result)
    sar = by_type.get("SAR")
    if isinstance(sar, dict):
        return bool(sar.get("narrative_required", True))
    if "SAR" not in report_types:
        return False
    return True


def build_timeline(case_or_status: dict[str, Any]) -> list[dict[str, Any]]:
    status = str(case_or_status.get("status", "pending")).lower()
    current = str(case_or_status.get("current_agent") or "").lower()
    result = _result_payload(case_or_status)
    report_types = _report_types(case_or_status)
    final_payload = result.get("final") if isinstance(result.get("final"), dict) else {}
    narrative_required = _narrative_required(result, report_types)

    descriptions = {
        "router": (result.get("router") or {}).get("reasoning", "Classifying filing type"),
        "aggregator": "Mapping case data to required report schema",
        "narrative": "Generating SAR narrative section",
        "validator": ((result.get("validation") or {}).get("status") or "Running compliance validation"),
        "filer": (final_payload.get("status") or "Preparing PDF output"),
    }

    out: list[dict[str, Any]] = []
    current_index = AGENT_ORDER.index(current) if current in AGENT_ORDER else -1
    needs_review = str(final_payload.get("status", "")).lower() == "needs_review"

    for index, agent in enumerate(AGENT_ORDER):
        if agent == "narrative" and not narrative_required:
            state = "skipped"
            desc = "Narrative not required for this report type"
        elif status == "completed":
            if agent == "filer" and needs_review:
                state = "skipped"
                desc = "Skipped because validation requires human review"
            else:
                state = "completed"
                desc = descriptions.get(agent, "")
        elif status in {"failed", "error"}:
            if current == agent:
                state = "error"
            elif current_index >= 0 and index < current_index:
                state = "completed"
            else:
                state = "pending"
            desc = descriptions.get(agent, "")
        else:
            if current == agent:
                state = "active"
            elif current_index >= 0 and index < current_index:
                state = "completed"
            else:
                state = "pending"
            desc = descriptions.get(agent, "")

        out.append(
            {
                "name": agent.title(),
                "status": state,
                "description": desc,
            }
        )
    return out


def _render_validation_panel(validation: dict[str, Any], aggregate: dict[str, Any]) -> None:
    if not validation:
        st.info("Validation output is not available yet.")
        return

    v_status = str(validation.get("status", "unknown")).upper()
    status_badge(v_status.lower().replace(" ", "_"), label=v_status)
    st.write("")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Completeness Score", f"{float(validation.get('completeness_score') or 0):.1f}")
    with c2:
        st.metric("Narrative Quality", f"{float(validation.get('narrative_quality_score') or 0):.1f}")
    with c3:
        approval = bool(validation.get("approval_flag"))
        st.metric("Approval", "PASS" if approval else "NEEDS REVIEW")

    checks = validation.get("compliance_checks")
    if isinstance(checks, dict) and checks:
        st.markdown("#### Validation Criteria")
        rows = [{"Criterion": key, "Result": value} for key, value in checks.items()]
        st.dataframe(rows, hide_index=True, use_container_width=True)

    issues = validation.get("issues")
    if isinstance(issues, list) and issues:
        st.markdown("#### Validation Issues")
        for issue in issues:
            st.warning(str(issue))

    recommendations = validation.get("recommendations")
    if isinstance(recommendations, list) and recommendations:
        st.markdown("#### Recommended Actions")
        for rec in recommendations:
            st.write(f"- {rec}")

    if not bool(validation.get("approval_flag")):
        st.error("Needs human review before filing.")
        st.markdown("#### Case Summary for Reviewer")
        st.write(f"Case ID: {aggregate.get('case_id', '-')}")
        st.write(f"Customer: {aggregate.get('customer_name', aggregate.get('subject', {}).get('name', '-'))}")
        st.write(f"Report Type: {aggregate.get('report_type', '-')}")
        st.write(f"Total Amount: {format_currency(aggregate.get('total_amount_involved', 0))}")
        st.write(f"Risk Score: {aggregate.get('risk_score', '-')}")


def _render_case_overview(details: dict[str, Any]) -> None:
    result = _result_payload(details)
    report_types = _report_types(details)
    aggregate = _primary_aggregate(result)
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    narrative = result.get("narrative") if isinstance(result.get("narrative"), dict) else {}
    final_payload = result.get("final") if isinstance(result.get("final"), dict) else {}
    display_status = "needs_review" if str(final_payload.get("status", "")).lower() == "needs_review" else str(details.get("status", "-"))

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Case ID", str(aggregate.get("case_id") or details.get("job_id") or "-"))
    with c2:
        st.metric("Report Types", ", ".join(report_types) if report_types else "-")
    with c3:
        st.metric("Amount", format_currency(aggregate.get("total_amount_involved", 0)))
    with c4:
        st.metric("Status", str(display_status).upper())

    if narrative and narrative.get("narrative_text"):
        with st.expander("Narrative Output", expanded=False):
            st.write(narrative.get("narrative_text"))

    _render_validation_panel(validation, aggregate)

    with st.expander("Raw Result JSON", expanded=False):
        st.json(details)


def _render_downloads(api_client: APIClient, details: dict[str, Any]) -> None:
    if str(details.get("status", "")).lower() != "completed":
        st.info("Report download is available once processing is completed.")
        return
    result = _result_payload(details)
    final_payload = result.get("final") if isinstance(result.get("final"), dict) else {}
    if str(final_payload.get("status", "")).lower() == "needs_review":
        st.warning("This case requires human review. No report was filed.")
        return

    reports: list[dict[str, Any]] = []
    if isinstance(final_payload.get("reports"), list):
        reports = [item for item in final_payload["reports"] if isinstance(item, dict)]
    elif final_payload.get("pdf_path"):
        reports = [final_payload]

    if not reports:
        st.info("No downloadable report artifacts found for this case.")
        return

    st.markdown("#### Generated Reports")
    st.table(
        [
            {
                "Report Type": report.get("report_type", ""),
                "Fields Filled": report.get("fields_filled", ""),
                "Attempted Fields": report.get("attempted_fields", ""),
                "Template Fields": report.get("template_field_count", ""),
                "Template": report.get("template_variant", report.get("template_path", "")),
                "Generated At": report.get("generated_at", ""),
            }
            for report in reports
        ]
    )

    for report in reports:
        report_type = str(report.get("report_type", "")).upper()
        if not report_type:
            continue
        button_key = f"download_{details.get('job_id')}_{report_type}"
        if st.button(f"Download {report_type} PDF", key=button_key):
            try:
                pdf_bytes = api_client.download_report(details["job_id"], report_type=report_type)
                st.download_button(
                    label=f"Save {report_type} PDF",
                    data=pdf_bytes,
                    file_name=f"{report_type}_{details.get('job_id')}.pdf",
                    mime="application/pdf",
                    key=f"{button_key}_save",
                )
            except APIClientError as exc:
                st.error(str(exc))


st.set_page_config(
    page_title="Case Management",
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
render_header("Case Management", "Monitor and manage compliance cases")

f1, f2, f3, f4 = st.columns([1, 1, 1, 2])
with f1:
    status_filter = st.selectbox("Status", ["All", "submitted", "processing", "completed", "failed", "needs_review"])
with f2:
    report_filter = st.selectbox("Report Type", ["All", "SAR", "CTR"])
with f3:
    auto_refresh = st.toggle("Auto-refresh", value=False)
with f4:
    query = st.text_input("Search by Case ID or Subject")

if st.button("Refresh Cases", type="secondary"):
    st.rerun()

try:
    cases = api_client.list_cases(
        tracked_job_ids=st.session_state.get("tracked_jobs", []),
        filters={"status": status_filter, "report_type": report_filter, "query": query},
    )
except APIClientError as exc:
    st.error(str(exc))
    cases = []

if not cases:
    st.info("No cases found.")

per_page = int(st.session_state.get("display_cases_per_page", 15))
max_page = max(1, (len(cases) + per_page - 1) // per_page)
page = st.number_input("Page", min_value=1, max_value=max_page, value=1)
start = (page - 1) * per_page
end = start + per_page

for case in cases[start:end]:
    view, download = render_case_card(case, key_prefix="case_mgmt")
    if view:
        st.session_state["selected_job_id"] = case.get("job_id")
    if download:
        st.session_state["download_job_id"] = case.get("job_id")

selected_job = st.session_state.get("selected_job_id")
if selected_job:
    st.markdown("---")
    st.markdown(f"### Case Details - {selected_job}")
    try:
        details = api_client.get_job_status(selected_job)
    except APIClientError as exc:
        st.error(str(exc))
        details = {}

    if details:
        tab1, tab2, tab3, tab4 = st.tabs(["Overview", "Agent Timeline", "Reports", "Audit Log"])
        with tab1:
            _render_case_overview(details)
        with tab2:
            render_agent_timeline(build_timeline(details))
        with tab3:
            _render_downloads(api_client, details)
        with tab4:
            result = _result_payload(details)
            rows = []
            for key in ("router", "aggregator_by_type", "narrative", "validation", "final"):
                if key in result:
                    rows.append({"step": key, "details": json.dumps(result.get(key), default=str)[:300]})
            st.dataframe(rows, hide_index=True, use_container_width=True)

if auto_refresh:
    time.sleep(max(int(st.session_state.get("notifications_refresh_seconds", 5)), 2))
    st.rerun()
