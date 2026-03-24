"""Runtime settings for Streamlit frontend."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class AppSettings:
    # On Streamlit Cloud, secrets are injected as env vars automatically.
    api_base_url: str = os.getenv("API_BASE_URL", "http://localhost:8001")
    request_timeout_seconds: int = int(os.getenv("STREAMLIT_TIMEOUT", "30"))
    request_retries: int = int(os.getenv("STREAMLIT_RETRIES", "2"))
    page_title: str = "BNY Mellon Compliance AI"
    page_icon: str = "streamlit_app/assets/favicon.ico"
    layout: str = "wide"


settings = AppSettings()
