from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from streamlit_app.components.header import load_styles, render_header
from streamlit_app.components.search_results import render_search_result
from streamlit_app.components.sidebar import render_sidebar
from streamlit_app.config.settings import settings
from streamlit_app.utils.api_client import APIClient, APIClientError
from streamlit_app.utils.session_state import init_session_state


st.set_page_config(
    page_title="Knowledge Base",
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
render_header("Knowledge Base", "Search regulations and compliance guidance")

query = st.text_input("Search Query", placeholder="When is a SAR required?")
collection = st.selectbox("Collection", ["regulations", "narratives"])
limit = st.number_input("Result Limit", min_value=1, max_value=20, value=5)

s1, s2, s3, s4, s5 = st.columns(5)
suggestions = [
    "When is a SAR required?",
    "CTR filing threshold",
    "Structuring definition",
    "Insider abuse reporting",
    "October 2025 guidance updates",
]
for idx, text in enumerate(suggestions):
    if [s1, s2, s3, s4, s5][idx].button(text, key=f"suggest_{idx}"):
        st.session_state["kb_query"] = text
        query = text

if "kb_query" in st.session_state and not query:
    query = st.session_state["kb_query"]

results = []
if st.button("Search", use_container_width=True):
    if not query.strip():
        st.warning("Enter a search query.")
    else:
        try:
            results = api_client.search_kb(query=query, collection=collection, limit=int(limit))
        except APIClientError as exc:
            st.error(str(exc))

if results:
    st.markdown(f"### Search Results ({len(results)})")
    for i, item in enumerate(results):
        render_search_result(item, key_prefix=f"res_{i}")
else:
    st.info("No results yet. Run a query to see relevant guidance.")
