"""Metric card renderer."""

from __future__ import annotations

import streamlit as st


def render_metric_card(title: str, value: str, delta: str | None = None, help_text: str | None = None) -> None:
    delta_html = f'<div class="metric-delta">{delta}</div>' if delta else ""
    help_html = f'<div class="metric-help">{help_text}</div>' if help_text else ""
    st.markdown(
        f"""
        <div class="metric-card">
          <div class="metric-title">{title}</div>
          <div class="metric-value">{value}</div>
          {delta_html}
          {help_html}
        </div>
        """,
        unsafe_allow_html=True,
    )
