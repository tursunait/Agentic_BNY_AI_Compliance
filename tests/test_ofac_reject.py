"""Tests for OFAC_REJECT report type: routing, KB fallback, validation, and sample JSON."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from narrative_agent import generate_narrative, validate_narrative
from narrative_agent.schemas import validate_input


def _load_ofac_example() -> dict:
    path = Path(__file__).resolve().parent.parent / "examples" / "ofac_reject_example.json"
    with open(path) as f:
        return json.load(f)


def test_validate_input_ofac_sample():
    """Sample OFAC JSON has required keys for OFAC_REJECT."""
    data = _load_ofac_example()
    assert validate_input(data, report_type_code="OFAC_REJECT") == data
    assert "case_id" in data and data["case_id"] == "OFAC-SYN-001"
    assert "transaction" in data and "case_facts" in data


@patch("narrative_agent.agent.build_narrative_guidance_context")
@patch("narrative_agent.agent.create_crew")
def test_generate_narrative_ofac_sample_mocked(mock_create_crew, mock_kb):
    """Generate narrative from sample OFAC JSON (mocked LLM) returns input + narrative."""
    data = _load_ofac_example()
    mock_kb.return_value = ("OFAC instructions", "OFAC examples")
    example_narrative = (
        "On March 6, 2026, First National Bank rejected an outbound wire transfer in the amount of 18,450.00 USD "
        "from ABC Import Services destined for Tehran Industrial Supply Co., with beneficiary financial institution "
        "Bank Melli Iran. The transaction was flagged by automated screening due to the beneficiary country (Iran). "
        "Bank Melli Iran is a sanctioned entity under the Iran Transactions and Sanctions Regulations (ITSR), 31 C.F.R. Part 560. "
        "The institution reviewed the payment message, invoice INV-2026-0112, customer account records, and sanctions screening output. "
        "Based on the sanctions nexus, the transaction was rejected and was not processed."
    )
    mock_result = MagicMock()
    mock_result.tasks_output = [MagicMock(raw=json.dumps({"narrative": example_narrative}))]
    mock_crew = MagicMock()
    mock_crew.kickoff.return_value = mock_result
    mock_create_crew.return_value = mock_crew

    result = generate_narrative(data, verbose=False)

    assert "narrative" in result
    assert result["case_id"] == "OFAC-SYN-001"
    assert result["report_type_code"] == "OFAC_REJECT"
    assert "rejected" in result["narrative"] and "not processed" in result["narrative"]
    mock_create_crew.assert_called_once_with(data, report_type_code="OFAC_REJECT", verbose=False)


def test_validate_narrative_ofac_reject_pass():
    """OFAC_REJECT narrative that states rejection and not blocked passes validation."""
    input_data = _load_ofac_example()
    narrative = (
        "On March 6, 2026, First National Bank rejected an outbound wire transfer of 18,450.00 USD from ABC Import Services "
        "to Tehran Industrial Supply Co. via Bank Melli Iran. Automated screening flagged the beneficiary country Iran. "
        "Bank Melli Iran is a sanctioned entity under Iran — ITSR. The institution reviewed the payment message, "
        "invoice INV-2026-0112, customer account records, and sanctions screening output. The transaction was rejected and was not processed."
    )
    result = validate_narrative(narrative, input_data, report_type_code="OFAC_REJECT")
    assert result.passed
    assert all(c.passed for c in result.checks)


def test_validate_narrative_ofac_reject_fail_blocking_language():
    """OFAC_REJECT narrative that says 'blocked' fails rejection_not_blocking check."""
    input_data = _load_ofac_example()
    narrative = (
        "The transaction was blocked due to sanctions. First National Bank blocked the transaction. "
        "We reviewed the payment message. The transaction was blocked."
    )
    result = validate_narrative(narrative, input_data, report_type_code="OFAC_REJECT")
    failed = result.failed_checks()
    assert any(c.name == "rejection_not_blocking" for c in failed)


def test_validate_narrative_ofac_reject_fail_rejection_not_stated():
    """OFAC_REJECT narrative that never says rejected/not processed fails rejection_stated."""
    input_data = _load_ofac_example()
    narrative = (
        "First National Bank reviewed a wire to Iran. The beneficiary was in a sanctioned jurisdiction. "
        "Documents were reviewed. No further action was taken."
    )
    result = validate_narrative(narrative, input_data, report_type_code="OFAC_REJECT")
    failed = result.failed_checks()
    assert any(c.name == "rejection_stated" for c in failed)
