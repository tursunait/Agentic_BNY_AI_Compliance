"""Case card component."""

from __future__ import annotations

import streamlit as st

from streamlit_app.components.status_badge import report_type_badge, risk_level_badge, status_badge
from streamlit_app.utils.formatting import format_currency, format_datetime


def render_case_card(case: dict, key_prefix: str = "case") -> tuple[bool, bool]:
    case_id = case.get("case_id", "N/A")
    subject = case.get("subject_name", "Unknown")
    amount = format_currency(case.get("amount_usd", 0))
    status = case.get("status", "pending")
    report_type = case.get("report_type", "SAR")
    created_at = format_datetime(case.get("created_at"), "default")
    risk_score = float(case.get("risk_score") or 0)

    with st.container(border=True):
        top1, top2 = st.columns([0.7, 0.3])
        with top1:
            st.markdown(f"#### {case_id}")
            st.caption(subject)
        with top2:
            report_type_badge(report_type if report_type != "BOTH" else "SAR")
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            status_badge(status)

        meta1, meta2, meta3 = st.columns(3)
        with meta1:
            st.write(f"**Amount:** {amount}")
        with meta2:
            st.write("**Risk:**")
            risk_level_badge(risk_score)
        with meta3:
            st.write(f"**Created:** {created_at}")

        b1, b2 = st.columns(2)
        with b1:
            view = st.button("View Details", key=f"{key_prefix}_view_{case.get('job_id', case_id)}")
        with b2:
            download = st.button(
                "Download Report",
                key=f"{key_prefix}_dl_{case.get('job_id', case_id)}",
                disabled=status != "completed",
            )
    return view, download
