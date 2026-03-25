"""PDF preview component."""

from __future__ import annotations

import base64

import streamlit as st


def render_pdf_preview(pdf_bytes: bytes, filename: str) -> None:
    if not pdf_bytes:
        st.warning("No PDF content available")
        return

    st.download_button(
        label=f"Download {filename}",
        data=pdf_bytes,
        file_name=filename,
        mime="application/pdf",
        use_container_width=True,
    )
    b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    iframe = f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="700" type="application/pdf"></iframe>'
    st.markdown(iframe, unsafe_allow_html=True)
