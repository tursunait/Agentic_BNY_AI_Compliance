"""Knowledge base search result components."""

from __future__ import annotations

import streamlit as st

from streamlit_app.components.status_badge import report_type_badge


def render_search_result(result: dict, key_prefix: str) -> None:
    title = result.get("section_title") or result.get("title") or "Untitled"
    text = result.get("text") or result.get("content") or ""
    category = result.get("category") or "general"
    report_types = result.get("report_types") or [result.get("report_type") or "SAR"]
    score = result.get("relevance_score") or result.get("distance") or result.get("score")
    source = result.get("regulation_id") or result.get("source") or "N/A"

    snippet = text if len(text) < 240 else text[:240] + "..."

    with st.container(border=True):
        st.markdown(f"### {title}")
        col1, col2 = st.columns([0.6, 0.4])
        with col1:
            for item in report_types:
                report_type_badge(str(item))
        with col2:
            st.caption(category.replace("_", " ").title())

        st.write(snippet)
        if score is not None:
            st.caption(f"Relevance: {float(score):.3f} | Source: {source}")
        else:
            st.caption(f"Source: {source}")

        with st.expander("View Full Regulation"):
            st.write(text or "No full text available")

        citation = f"{source} - {title}"
        st.code(citation)
        st.caption(f"Key: {key_prefix}")
