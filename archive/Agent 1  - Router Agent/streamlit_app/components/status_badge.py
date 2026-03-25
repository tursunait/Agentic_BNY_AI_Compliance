"""Status, report-type, and risk badges."""

from __future__ import annotations

import streamlit as st


def _badge(css_class: str, label: str) -> None:
    st.markdown(f'<span class="{css_class}">{label}</span>', unsafe_allow_html=True)


def status_badge(status: str, label: str | None = None) -> None:
    normalized = (status or "pending").lower().replace("_", "-")
    _badge(f"status-badge {normalized}", label or normalized.replace("-", " ").title())


def report_type_badge(report_type: str) -> None:
    normalized = (report_type or "sar").lower()
    _badge(f"report-badge {normalized}", normalized.upper())


def risk_level_badge(risk_score: float) -> None:
    score = float(risk_score or 0)
    if score >= 0.8:
        level, css = "HIGH", "risk-high"
    elif score >= 0.5:
        level, css = "MEDIUM", "risk-medium"
    elif score >= 0.3:
        level, css = "LOW", "risk-low"
    else:
        level, css = "MINIMAL", "risk-none"
    _badge(f"risk-badge {css}", f"{level} ({score:.2f})")
