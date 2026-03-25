"""Session state initialization and helpers."""

from __future__ import annotations

import streamlit as st

from streamlit_app.config.settings import settings


def init_session_state() -> None:
    defaults = {
        "api_client": None,
        "user": {
            "name": "Compliance Officer",
            "role": "Senior Analyst",
            "department": "AML/BSA Compliance",
            "email": "compliance@bnymellon.com",
        },
        "settings_api_url": settings.api_base_url,
        "settings_timeout": settings.request_timeout_seconds,
        "settings_retry_enabled": True,
        "display_cases_per_page": 15,
        "display_animations": True,
        "display_date_format": "MM/DD/YYYY",
        "display_currency_format": "$1,234.56",
        "notifications_case_complete": True,
        "notifications_validation_fail": True,
        "notifications_daily_summary": False,
        "notifications_refresh_seconds": 5,
        "tracked_jobs": [],
        "selected_job_id": None,
        "css_loaded": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def save_setting(key: str, value) -> None:
    st.session_state[f"settings_{key}"] = value


def add_tracked_job(job_id: str) -> None:
    jobs = st.session_state.get("tracked_jobs", [])
    if job_id and job_id not in jobs:
        jobs.append(job_id)
    st.session_state["tracked_jobs"] = jobs
