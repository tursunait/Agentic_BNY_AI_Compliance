"""Sidebar navigation and health widget."""

from __future__ import annotations

import streamlit as st

from streamlit_app.utils.api_client import APIClient, APIClientError


def render_sidebar(api_client: APIClient) -> None:
    with st.sidebar:
        st.markdown("## Navigation")
        # Paths must be relative to the Streamlit main script directory.
        st.page_link("app.py", label="Home")
        st.page_link("pages/1_Dashboard.py", label="Dashboard")
        st.page_link("pages/2_Submit_Case.py", label="Submit Case")
        st.page_link("pages/3_Case_Management.py", label="Case Management")
        st.page_link("pages/6_Settings.py", label="Settings")

        st.markdown("---")
        st.markdown("### Backend Health")
        try:
            health = api_client.health_check()
            if health.get("status") == "healthy":
                st.success("Connected")
            services = health.get("services", {}) if isinstance(health, dict) else {}
            for svc in ("database", "weaviate", "redis"):
                if services.get(svc) is True:
                    st.caption(f"{svc}: up")
                elif services.get(svc) is False:
                    st.caption(f"{svc}: down")
        except APIClientError:
            st.error("Backend unavailable")

        st.markdown("---")
        st.caption(f"Tracked jobs: {len(st.session_state.get('tracked_jobs', []))}")
