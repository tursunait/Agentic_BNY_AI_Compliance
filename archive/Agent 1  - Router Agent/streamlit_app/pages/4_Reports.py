from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from streamlit_app.components.header import load_styles, render_header
from streamlit_app.components.report_preview import render_pdf_preview
from streamlit_app.components.sidebar import render_sidebar
from streamlit_app.components.status_badge import report_type_badge, status_badge
from streamlit_app.config.settings import settings
from streamlit_app.utils.api_client import APIClient, APIClientError
from streamlit_app.utils.formatting import format_datetime
from streamlit_app.utils.session_state import init_session_state


st.set_page_config(
    page_title="Reports",
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
render_header("Reports Library", "Browse and download generated compliance reports")

left, right = st.columns([1, 3])
with left:
    report_type = st.selectbox("Report Type", ["All", "SAR", "CTR"])
    status = st.selectbox("Status", ["All", "success", "completed", "processing", "failed"])
    query = st.text_input("Search")

try:
    reports = api_client.list_reports(
        tracked_job_ids=st.session_state.get("tracked_jobs", []),
        filters={"report_type": report_type, "status": status, "query": query},
    )
except APIClientError as exc:
    st.error(str(exc))
    reports = []

preview_report = None

with right:
    if not reports:
        st.info("No reports found.")
    for idx, report in enumerate(reports):
        key = f"report_{idx}_{report.get('job_id')}"
        with st.container(border=True):
            c1, c2, c3 = st.columns([0.6, 0.2, 0.2])
            with c1:
                st.markdown(f"**{report.get('filename', 'report.pdf')}**")
                st.caption(f"Case: {report.get('case_id')} | Generated: {format_datetime(report.get('generated_at'))}")
                st.caption(f"Fields filled: {report.get('fields_filled', 0)}")
            with c2:
                report_type_badge(report.get("report_type", "SAR"))
                status_badge(report.get("status", "completed"))
            with c3:
                preview = st.button("Preview", key=f"{key}_preview")
                download = st.button("Download", key=f"{key}_download")

            if preview:
                preview_report = report
            if download:
                try:
                    pdf = api_client.download_report(report.get("job_id"), report.get("report_type"))
                    st.download_button(
                        "Download PDF",
                        data=pdf,
                        file_name=report.get("filename", "report.pdf"),
                        mime="application/pdf",
                        key=f"{key}_download_file",
                    )
                except APIClientError as exc:
                    st.error(str(exc))

if preview_report:
    st.markdown("---")
    st.markdown(f"### Preview - {preview_report.get('filename')}")
    pdf_bytes = b""
    pdf_path = preview_report.get("pdf_path")
    if pdf_path and Path(str(pdf_path)).exists():
        pdf_bytes = Path(str(pdf_path)).read_bytes()
    else:
        try:
            pdf_bytes = api_client.download_report(preview_report.get("job_id"), preview_report.get("report_type"))
        except APIClientError as exc:
            st.error(str(exc))

    if pdf_bytes:
        render_pdf_preview(pdf_bytes, preview_report.get("filename", "report.pdf"))
