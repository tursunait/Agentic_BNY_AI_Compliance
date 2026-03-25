"""Tests for the Narrative Generator Agent."""

from unittest.mock import MagicMock, patch

import pytest

from narrative_agent.agent import (
    _build_task_description,
    _parse_narrative_output,
    create_crew,
    generate_narrative,
)
from narrative_agent.knowledge_base import KnowledgeBaseError
from narrative_agent.schemas import validate_input


def test_parse_narrative_output_pure_json():
    raw = '{"narrative": "The subject made multiple cash deposits."}'
    out = _parse_narrative_output(raw)
    assert out.narrative == "The subject made multiple cash deposits."


def test_parse_narrative_output_with_markdown():
    raw = 'Here is the output:\n```json\n{"narrative": "A factual SAR narrative."}\n```'
    out = _parse_narrative_output(raw)
    assert out.narrative == "A factual SAR narrative."


def test_parse_narrative_output_extra_text():
    raw = 'Some prefix {"narrative": "Only this counts."} trailing'
    out = _parse_narrative_output(raw)
    assert out.narrative == "Only this counts."


@patch("narrative_agent.agent.build_narrative_guidance_context")
@patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-dummy"}, clear=False)
def test_create_crew_returns_crew(mock_kb):
    """Crew is created using narrative guidance from Supabase (mocked here)."""
    mock_kb.return_value = ("Instructions from KB", "Examples from KB")
    input_json = {
        "case_id": "C-1",
        "subject": {"name": "Test"},
        "SuspiciousActivityInformation": {},
    }
    crew = create_crew(input_json, verbose=False)
    assert crew is not None
    assert len(crew.agents) == 1
    assert len(crew.tasks) == 1
    mock_kb.assert_called_once_with("SAR")


@patch("narrative_agent.agent.build_narrative_guidance_context")
@patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-dummy"}, clear=False)
def test_create_crew_ofac_reject_uses_spec(mock_kb):
    """OFAC_REJECT crew uses agent role/goal from report type spec."""
    mock_kb.return_value = ("OFAC instructions", "OFAC examples")
    input_json = {
        "case_id": "OFAC-001",
        "transaction": {},
        "case_facts": {},
    }
    crew = create_crew(input_json, report_type_code="OFAC_REJECT", verbose=False)
    assert crew is not None
    assert len(crew.agents) == 1
    agent = crew.agents[0]
    assert "OFAC" in agent.role
    mock_kb.assert_called_once_with("OFAC_REJECT")


@patch("narrative_agent.agent.build_narrative_guidance_context")
@patch("narrative_agent.agent.create_crew")
def test_generate_narrative_mocked_crew(mock_create_crew, mock_kb):
    input_data = {
        "case_id": "CASE-2024-677021",
        "subject": {"subject_id": "C-94926", "name": "Global Trade Corp", "type": "Individual"},
        "SuspiciousActivityInformation": {
            "26_AmountInvolved": {"amount_usd": 25500.0},
            "27_DateOrDateRange": {"from": "03/15/2024", "to": "03/22/2024"},
        },
    }
    mock_kb.return_value = ("KB instructions", "KB examples")
    mock_result = MagicMock()
    mock_result.tasks_output = [MagicMock(raw='{"narrative": "Generated narrative for Global Trade Corp."}')]
    mock_crew = MagicMock()
    mock_crew.kickoff.return_value = mock_result
    mock_create_crew.return_value = mock_crew

    result = generate_narrative(input_data, verbose=False)

    # Output is input + narrative (same format as input, one new field)
    assert result["narrative"] == "Generated narrative for Global Trade Corp."
    assert result["case_id"] == input_data["case_id"]
    assert result["subject"] == input_data["subject"]
    assert set(result.keys()) == set(input_data.keys()) | {"narrative"}
    mock_create_crew.assert_called_once_with(
        input_data, report_type_code="SAR", verbose=False
    )
    mock_crew.kickoff.assert_called_once()


@patch("narrative_agent.agent.build_narrative_guidance_context")
@patch("narrative_agent.agent.create_crew")
def test_generate_narrative_ofac_reject_routing(mock_create_crew, mock_kb):
    """OFAC_REJECT: report_type_code from input routes to correct KB and crew."""
    input_data = {
        "case_id": "OFAC-SYN-001",
        "report_type_code": "OFAC_REJECT",
        "transaction": {"amount_rejected": "18450.00", "currency": "USD"},
        "case_facts": {"disposition": "Rejected — transaction not processed"},
    }
    mock_kb.return_value = ("OFAC instructions", "OFAC examples")
    mock_result = MagicMock()
    mock_result.tasks_output = [MagicMock(raw='{"narrative": "First National Bank rejected the wire. Transaction was not processed."}')]
    mock_crew = MagicMock()
    mock_crew.kickoff.return_value = mock_result
    mock_create_crew.return_value = mock_crew

    result = generate_narrative(input_data, verbose=False)

    assert result["narrative"] == "First National Bank rejected the wire. Transaction was not processed."
    assert result["case_id"] == "OFAC-SYN-001"
    mock_create_crew.assert_called_once_with(
        input_data, report_type_code="OFAC_REJECT", verbose=False
    )


def test_generate_narrative_validates_input():
    with pytest.raises(ValueError, match="required keys"):
        generate_narrative({"case_id": "C-1"})  # missing subject and SuspiciousActivityInformation


@patch("narrative_agent.agent.build_narrative_guidance_context")
def test_task_description_uses_supabase_instructions_only(mock_kb):
    """Task description contains only content from Supabase, not local few-shot."""
    mock_kb.return_value = (
        "INSTRUCTIONS_FROM_SUPABASE_REPORT_TYPES",
        "EXAMPLES_FROM_SUPABASE_NARRATIVE_EXAMPLES",
    )
    input_json = {
        "case_id": "C-1",
        "subject": {"name": "Test"},
        "SuspiciousActivityInformation": {},
    }
    desc = _build_task_description(input_json, report_type_code="SAR")
    assert "INSTRUCTIONS_FROM_SUPABASE_REPORT_TYPES" in desc
    assert "EXAMPLES_FROM_SUPABASE_NARRATIVE_EXAMPLES" in desc
    mock_kb.assert_called_once_with("SAR")


@patch("narrative_agent.agent.build_narrative_guidance_context")
def test_build_task_description_raises_when_kb_fails(mock_kb):
    """When Supabase KB raises, _build_task_description propagates the error."""
    mock_kb.side_effect = KnowledgeBaseError("SUPABASE_URL is not set")
    input_json = {
        "case_id": "C-1",
        "subject": {"name": "Test"},
        "SuspiciousActivityInformation": {},
    }
    with pytest.raises(KnowledgeBaseError, match="SUPABASE_URL"):
        _build_task_description(input_json, report_type_code="SAR")


@patch("narrative_agent.agent.build_narrative_guidance_context")
@patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-dummy"}, clear=False)
def test_generate_narrative_raises_when_kb_fails(mock_kb):
    """When Supabase KB is unavailable, generate_narrative raises KnowledgeBaseError."""
    mock_kb.side_effect = KnowledgeBaseError("Failed to fetch report_types")
    input_data = {
        "case_id": "C-1",
        "subject": {"name": "Test"},
        "SuspiciousActivityInformation": {},
    }
    with pytest.raises(KnowledgeBaseError, match="Failed to fetch"):
        generate_narrative(input_data, report_type_code="SAR", verbose=False)
