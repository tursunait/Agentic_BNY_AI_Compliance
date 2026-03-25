from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from streamlit_app.components.header import load_styles, render_header
from streamlit_app.components.sidebar import render_sidebar
from streamlit_app.config.settings import settings
from streamlit_app.utils.api_client import APIClient, APIClientError
from streamlit_app.utils.session_state import init_session_state, save_setting


st.set_page_config(
    page_title="Settings",
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
render_header("Settings", "System configuration and preferences")

api_tab, user_tab, notif_tab, display_tab = st.tabs(["API", "User", "Notifications", "Display"])

with api_tab:
    api_url = st.text_input("Backend API URL", value=st.session_state.get("settings_api_url"))
    timeout = st.number_input("Request Timeout", min_value=10, max_value=120, value=int(st.session_state.get("settings_timeout", 30)))
    retry_enabled = st.checkbox("Retry failed requests", value=bool(st.session_state.get("settings_retry_enabled", True)))

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Test Connection"):
            try:
                tester = APIClient(base_url=api_url, timeout=int(timeout), retries=2 if retry_enabled else 0)
                tester.health_check()
                st.success("Connected")
            except APIClientError as exc:
                st.error(str(exc))

    with c2:
        if st.button("Save API Settings"):
            save_setting("api_url", api_url)
            save_setting("timeout", int(timeout))
            save_setting("retry_enabled", retry_enabled)
            st.session_state["api_client"] = APIClient(base_url=api_url, timeout=int(timeout), retries=2 if retry_enabled else 0)
            st.success("API settings saved")

with user_tab:
    user = st.session_state.get("user", {})
    name = st.text_input("Full Name", value=user.get("name", ""))
    role = st.text_input("Role", value=user.get("role", ""))
    department = st.text_input("Department", value=user.get("department", ""))
    email = st.text_input("Email", value=user.get("email", ""))
    if st.button("Update Profile"):
        st.session_state["user"] = {
            "name": name,
            "role": role,
            "department": department,
            "email": email,
        }
        st.success("Profile updated")

with notif_tab:
    case_complete = st.checkbox("Email notifications for case completion", value=st.session_state.get("notifications_case_complete", True))
    validation_fail = st.checkbox("In-app notifications for validation failures", value=st.session_state.get("notifications_validation_fail", True))
    daily_summary = st.checkbox("Daily summary reports", value=st.session_state.get("notifications_daily_summary", False))
    refresh_seconds = st.number_input("Auto-refresh interval (seconds)", min_value=2, max_value=60, value=int(st.session_state.get("notifications_refresh_seconds", 5)))
    if st.button("Save Notification Preferences"):
        st.session_state["notifications_case_complete"] = case_complete
        st.session_state["notifications_validation_fail"] = validation_fail
        st.session_state["notifications_daily_summary"] = daily_summary
        st.session_state["notifications_refresh_seconds"] = int(refresh_seconds)
        st.success("Notification preferences saved")

with display_tab:
    cases_per_page = st.slider("Cases per page", min_value=10, max_value=50, value=int(st.session_state.get("display_cases_per_page", 15)))
    animations = st.checkbox("Enable animations", value=bool(st.session_state.get("display_animations", True)))
    date_format = st.selectbox("Date format", ["MM/DD/YYYY", "DD/MM/YYYY", "YYYY-MM-DD"], index=0)
    currency_format = st.selectbox("Currency format", ["$1,234.56", "1,234.56 USD"], index=0)

    if st.button("Apply Display Settings"):
        st.session_state["display_cases_per_page"] = cases_per_page
        st.session_state["display_animations"] = animations
        st.session_state["display_date_format"] = date_format
        st.session_state["display_currency_format"] = currency_format
        st.success("Display settings applied")
