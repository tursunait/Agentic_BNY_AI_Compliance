from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from streamlit_app.components.header import load_styles, render_header
from streamlit_app.components.metric_card import render_metric_card
from streamlit_app.components.sidebar import render_sidebar
from streamlit_app.config.branding import BNYColors
from streamlit_app.config.settings import settings
from streamlit_app.utils.api_client import APIClient, APIClientError
from streamlit_app.utils.session_state import init_session_state


st.set_page_config(
    page_title="Dashboard",
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
render_header("Compliance Dashboard", "Real-time system analytics and insights")

left, right = st.columns(2)
with left:
    _from = st.date_input("From", value=date.today() - timedelta(days=30))
with right:
    _to = st.date_input("To", value=date.today())

try:
    metrics = api_client.get_dashboard_metrics(tracked_job_ids=st.session_state.get("tracked_jobs", []))
except APIClientError as exc:
    st.error(str(exc))
    metrics = {}

m1, m2, m3, m4 = st.columns(4)
with m1:
    render_metric_card("Total Cases", str(metrics.get("total_cases", 0)))
with m2:
    render_metric_card("SARs", str(metrics.get("sar_count", 0)))
with m3:
    render_metric_card("CTRs", str(metrics.get("ctr_count", 0)))
with m4:
    render_metric_card("Avg Time", f"{metrics.get('avg_processing_hours', 0):.1f}h")

status_dist = metrics.get("status_distribution", {})
status_df = pd.DataFrame(
    [
        {"Status": "Submitted", "Count": status_dist.get("submitted", 0)},
        {"Status": "Processing", "Count": status_dist.get("processing", 0)},
        {"Status": "Completed", "Count": status_dist.get("completed", 0)},
        {"Status": "Failed", "Count": status_dist.get("failed", 0)},
    ]
)

type_df = pd.DataFrame(
    [
        {"Type": "SAR", "Count": metrics.get("sar_count", 0)},
        {"Type": "CTR", "Count": metrics.get("ctr_count", 0)},
        {
            "Type": "Sanctions",
            "Count": max(metrics.get("total_cases", 0) - metrics.get("sar_count", 0) - metrics.get("ctr_count", 0), 0),
        },
    ]
)

c1, c2 = st.columns(2)
with c1:
    fig = px.pie(
        type_df,
        values="Count",
        names="Type",
        title="Cases by Report Type",
        color="Type",
        color_discrete_map={
            "SAR": BNYColors.SAR_COLOR,
            "CTR": BNYColors.CTR_COLOR,
            "Sanctions": BNYColors.SANCTIONS_COLOR,
        },
    )
    fig.update_layout(font={"family": "Helvetica Neue"}, paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)

with c2:
    bar = px.bar(
        status_df,
        x="Status",
        y="Count",
        title="Status Distribution",
        color="Status",
        color_discrete_sequence=[BNYColors.BNY_LIGHT_BLUE, BNYColors.BNY_TEAL, BNYColors.SUCCESS, BNYColors.DANGER],
    )
    bar.update_layout(showlegend=False, paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(bar, use_container_width=True)

trend = pd.date_range(_from, _to)
trend_df = pd.DataFrame(
    {
        "Date": trend,
        "SAR": [metrics.get("sar_count", 0) // max(len(trend), 1)] * len(trend),
        "CTR": [metrics.get("ctr_count", 0) // max(len(trend), 1)] * len(trend),
    }
).melt(id_vars="Date", value_vars=["SAR", "CTR"], var_name="Type", value_name="Count")

line = px.line(
    trend_df,
    x="Date",
    y="Count",
    color="Type",
    title="Cases Over Time",
    color_discrete_map={"SAR": BNYColors.SAR_COLOR, "CTR": BNYColors.CTR_COLOR},
)
line.update_layout(paper_bgcolor="rgba(0,0,0,0)")
st.plotly_chart(line, use_container_width=True)

st.markdown("### Agent Performance")
perf = metrics.get("agent_performance", [])
if perf:
    st.dataframe(pd.DataFrame(perf), hide_index=True, use_container_width=True)
else:
    st.info("No agent metrics available")
