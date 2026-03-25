"""Router agent configuration (model, labels)."""

import os

# OpenAI model for classification (GPT-4.1 mini)
ROUTER_LLM_MODEL = "gpt-4.1-mini"

# Supported report types (used for validation and prompts)
SUPPORTED_REPORT_TYPES = ["SAR", "CTR", "SANCTIONS", "BOTH"]

# Backend API base URL for full-pipeline submit (used by router_agent Streamlit app)
API_BASE_URL = os.environ.get("COMPLIANCE_API_BASE_URL", "http://localhost:8001")
