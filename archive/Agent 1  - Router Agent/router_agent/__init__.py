"""
Router Agent: entry-point agent for the compliance reporting pipeline.

- Classifies report type (SAR, CTR, Sanctions, etc.) from natural language or JSON input.
- Checks Supabase KB for report type and required form fields.
- Validates user input against the report's JSON schema and returns missing fields for UI prompting.
"""

from router_agent.run import run_router, RouterResult

__all__ = ["run_router", "RouterResult"]
