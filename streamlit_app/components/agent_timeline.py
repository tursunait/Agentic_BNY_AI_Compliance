"""Agent progress timeline component."""

from __future__ import annotations

import streamlit as st

from streamlit_app.components.status_badge import status_badge


def render_agent_timeline(agents: list[dict]) -> None:
    icons = {
        "completed": "Done",
        "active": "In Progress",
        "processing": "In Progress",
        "skipped": "Skipped",
        "error": "Error",
        "failed": "Error",
        "pending": "Pending",
    }
    for index, agent in enumerate(agents):
        status = str(agent.get("status", "pending")).lower()
        step_prefix = icons.get(status, "Pending")
        name = agent.get("name", "Unknown")
        duration = agent.get("duration", "")
        description = agent.get("description", "")
        with st.container(border=True):
            c1, c2 = st.columns([5, 2])
            with c1:
                st.markdown(f"**{step_prefix} - {name}**")
                if description:
                    st.caption(str(description))
                if duration:
                    st.caption(f"Duration: {duration}")
            with c2:
                status_badge(status)
        if index < len(agents) - 1:
            st.write("")
