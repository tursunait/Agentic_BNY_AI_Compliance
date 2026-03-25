"""Landing page for Compliance AI Streamlit frontend."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from streamlit_app.components.header import load_styles, render_header
from streamlit_app.components.metric_card import render_metric_card
from streamlit_app.components.sidebar import render_sidebar
from streamlit_app.config.settings import settings
from streamlit_app.utils.api_client import APIClient, APIClientError
from streamlit_app.utils.formatting import format_currency
from streamlit_app.utils.session_state import init_session_state


st.set_page_config(
    page_title=settings.page_title,
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
        retries=2 if st.session_state.get("settings_retry_enabled", True) else 0,
    )

api_client: APIClient = st.session_state["api_client"]
render_sidebar(api_client)
render_header("Compliance AI System")

metrics = {}
try:
    metrics = api_client.get_dashboard_metrics(tracked_job_ids=st.session_state.get("tracked_jobs", []))
except APIClientError as exc:
    st.error(str(exc))

st.markdown(
    """
    <div class="home-hero">
      <div class="home-hero-title">Compliance Workflow Control Center</div>
      <div class="home-hero-subtitle">
        Submit new cases, monitor processing status, and download generated filings from one place.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

m1, m2, m3, m4 = st.columns(4)
with m1:
    render_metric_card("Active Cases", str(metrics.get("active_cases", 0)), help_text="Currently in progress")
with m2:
    render_metric_card("Pending Reviews", str(metrics.get("pending_reviews", 0)), help_text="Requires analyst action")
with m3:
    render_metric_card("Reports Generated", str(metrics.get("reports_generated", 0)), help_text="Completed filings")
with m4:
    avg_min = metrics.get("avg_processing_minutes", 0)
    avg_label = f"{avg_min:.1f} min" if avg_min else "N/A"
    render_metric_card("Avg Processing", avg_label, help_text="Per case")

st.markdown('<div class="home-section-title">Recent Activity</div>', unsafe_allow_html=True)
try:
    recent = api_client.get_recent_cases(limit=5, tracked_job_ids=st.session_state.get("tracked_jobs", []))
except APIClientError:
    recent = []

if not recent:
    st.info("No recent cases found. Submit a case to begin.")
else:
    table = [
        {
            "Case ID": item.get("case_id"),
            "Job ID": item.get("job_id"),
            "Status": item.get("status"),
            "Report": item.get("report_type"),
            "Amount": format_currency(item.get("amount_usd", 0)),
        }
        for item in recent
    ]
    st.dataframe(table, hide_index=True, use_container_width=True)

st.markdown('<div class="home-section-title">Primary Action</div>', unsafe_allow_html=True)
if st.button("Submit New Case", use_container_width=True, type="primary"):
    st.switch_page("pages/2_Submit_Case.py")
