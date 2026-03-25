"""Header and stylesheet helpers."""

from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st


def _b64_image(path: Path) -> str:
    if not path.exists():
        return ""
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def load_styles(force: bool = False) -> None:
    css_dir = Path(__file__).resolve().parents[1] / "styles"
    css = ""
    for name in ("custom.css", "components.css"):
        path = css_dir / name
        if path.exists():
            css += path.read_text(encoding="utf-8")
    if css.strip():
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def render_header(title: str, subtitle: str = "Multi-Agent Suspicious Activity Detection") -> None:
    user = st.session_state.get("user", {})
    logo_path = Path(__file__).resolve().parents[1] / "assets" / "bny_logo_white.png"
    logo_b64 = _b64_image(logo_path)
    logo_html = (
        f'<img src="data:image/png;base64,{logo_b64}" alt="BNY" style="height:44px;"/>'
        if logo_b64
        else '<div class="logo-fallback">BNY</div>'
    )

    html = f"""
    <div class="app-header">
      <div class="app-header-left">
        {logo_html}
        <div>
          <div class="app-title">{title}</div>
          <div class="app-subtitle">{subtitle}</div>
        </div>
      </div>
      <div class="app-header-user">
        <div class="app-header-user-name">{user.get('name', 'Compliance Officer')}</div>
        <div class="app-header-user-role">{user.get('role', 'Analyst')}</div>
      </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)
