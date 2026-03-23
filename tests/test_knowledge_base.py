"""Tests for Supabase knowledge base connection and data fetching."""

from unittest.mock import patch

import pytest

from narrative_agent.knowledge_base import (
    KnowledgeBaseError,
    NarrativeExample,
    ReportTypeConfig,
    build_narrative_guidance_context,
    fetch_narrative_examples,
    fetch_report_type_config,
)


def test_fetch_report_type_config_raises_when_url_missing():
    """KB raises when SUPABASE_URL is not set."""
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(KnowledgeBaseError, match="SUPABASE_URL"):
            fetch_report_type_config("SAR")


def test_fetch_report_type_config_raises_when_key_missing():
    """KB raises when SUPABASE_ANON_KEY is not set."""
    with patch.dict(
        "os.environ",
        {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_ANON_KEY": ""},
        clear=False,
    ):
        with pytest.raises(KnowledgeBaseError, match="SUPABASE_ANON_KEY"):
            fetch_report_type_config("SAR")


@patch("narrative_agent.knowledge_base.requests.get")
def test_fetch_report_type_config_success(mock_get):
    """When Supabase returns 200 and a row, we parse ReportTypeConfig."""
    mock_get.return_value.status_code = 200
    mock_get.return_value.ok = True
    mock_get.return_value.json.return_value = [
        {
            "report_type_code": "SAR",
            "display_name": "Suspicious Activity Report",
            "regulatory_body": "FinCEN",
            "narrative_required": True,
            "narrative_instructions": "Generate a SAR narrative following FinCEN Part V.",
        }
    ]
    with patch.dict(
        "os.environ",
        {"SUPABASE_URL": "https://kb.supabase.co", "SUPABASE_ANON_KEY": "test-key"},
        clear=False,
    ):
        cfg = fetch_report_type_config("SAR")
    assert isinstance(cfg, ReportTypeConfig)
    assert cfg.report_type_code == "SAR"
    assert cfg.display_name == "Suspicious Activity Report"
    assert cfg.narrative_instructions == "Generate a SAR narrative following FinCEN Part V."
    mock_get.assert_called_once()
    call_url = mock_get.call_args[0][0]
    assert "report_types" in call_url


@patch("narrative_agent.knowledge_base.requests.get")
def test_fetch_report_type_config_raises_on_http_error(mock_get):
    """KB raises when Supabase returns non-2xx."""
    mock_get.return_value.ok = False
    mock_get.return_value.status_code = 500
    mock_get.return_value.text = "Internal Error"
    with patch.dict(
        "os.environ",
        {"SUPABASE_URL": "https://kb.supabase.co", "SUPABASE_ANON_KEY": "test-key"},
        clear=False,
    ):
        with pytest.raises(KnowledgeBaseError, match="500"):
            fetch_report_type_config("SAR")


@patch("narrative_agent.knowledge_base.requests.get")
def test_fetch_report_type_config_raises_when_no_rows(mock_get):
    """KB raises when no narrative-enabled report type exists for code."""
    mock_get.return_value.ok = True
    mock_get.return_value.json.return_value = []
    with patch.dict(
        "os.environ",
        {"SUPABASE_URL": "https://kb.supabase.co", "SUPABASE_ANON_KEY": "test-key"},
        clear=False,
    ):
        with pytest.raises(KnowledgeBaseError, match="No active narrative-enabled"):
            fetch_report_type_config("UNKNOWN")


@patch("narrative_agent.knowledge_base.requests.get")
def test_fetch_narrative_examples_success(mock_get):
    """When Supabase returns 200 and rows, we parse list of NarrativeExample."""
    mock_get.return_value.ok = True
    mock_get.return_value.json.return_value = [
        {
            "summary": "Structuring example",
            "narrative_text": "The subject made multiple deposits below the CTR threshold.",
            "effectiveness_notes": "Clear and factual.",
            "example_order": 1,
        }
    ]
    with patch.dict(
        "os.environ",
        {"SUPABASE_URL": "https://kb.supabase.co", "SUPABASE_ANON_KEY": "test-key"},
        clear=False,
    ):
        examples = fetch_narrative_examples("SAR")
    assert len(examples) == 1
    assert isinstance(examples[0], NarrativeExample)
    assert examples[0].summary == "Structuring example"
    assert "CTR threshold" in examples[0].narrative_text
    call_url = mock_get.call_args[0][0]
    assert "narrative_examples" in call_url


@patch("narrative_agent.knowledge_base.fetch_narrative_examples")
@patch("narrative_agent.knowledge_base.fetch_report_type_config")
def test_build_narrative_guidance_context_returns_supabase_content(
    mock_fetch_config, mock_fetch_examples
):
    """build_narrative_guidance_context returns instructions and examples from Supabase."""
    mock_fetch_config.return_value = ReportTypeConfig(
        report_type_code="SAR",
        narrative_instructions="INSTRUCTIONS_FROM_SUPABASE_TABLE",
        json_schema=None,
    )
    mock_fetch_examples.return_value = [
        NarrativeExample(
            summary="Example one",
            narrative_text="EXAMPLES_FROM_SUPABASE_TABLE",
            effectiveness_notes="Why it works.",
        )
    ]
    instructions, examples = build_narrative_guidance_context("SAR")
    assert "INSTRUCTIONS_FROM_SUPABASE_TABLE" in instructions
    assert "EXAMPLES_FROM_SUPABASE_TABLE" in examples
    assert "Example one" in examples
    mock_fetch_config.assert_called_once_with("SAR")
    mock_fetch_examples.assert_called_once_with("SAR")


@patch("narrative_agent.knowledge_base.fetch_report_type_config")
def test_build_narrative_guidance_context_ofac_reject_fallback(mock_fetch_config):
    """When KB has no row for OFAC_REJECT, local fallback returns instructions and examples."""
    mock_fetch_config.side_effect = KnowledgeBaseError(
        "No active narrative-enabled report_type found for code='OFAC_REJECT'."
    )
    instructions, examples = build_narrative_guidance_context("OFAC_REJECT")
    assert "Sanctions Rejected Transaction" in instructions or "rejected" in instructions.lower()
    assert "rejected" in examples.lower() or "not processed" in examples.lower()
    mock_fetch_config.assert_called_once_with("OFAC_REJECT")
