"""Tests for input/output schemas."""

import pytest
from pydantic import ValidationError

from narrative_agent.schemas import NarrativeOutput, validate_input, validate_output


def test_narrative_output_valid():
    out = NarrativeOutput(narrative="This is a SAR narrative.")
    assert out.narrative == "This is a SAR narrative."


def test_narrative_output_missing_narrative():
    with pytest.raises(ValidationError):
        NarrativeOutput()


def test_validate_input_ok():
    data = {"case_id": "C-1", "subject": {"name": "X"}, "SuspiciousActivityInformation": {}}
    assert validate_input(data) == data


def test_validate_input_missing_case_id():
    with pytest.raises(ValueError, match="case_id"):
        validate_input({"subject": {}, "SuspiciousActivityInformation": {}})


def test_validate_input_missing_subject():
    with pytest.raises(ValueError, match="subject"):
        validate_input({"case_id": "C-1", "SuspiciousActivityInformation": {}})


def test_validate_input_missing_suspicious_activity():
    with pytest.raises(ValueError, match="SuspiciousActivityInformation"):
        validate_input({"case_id": "C-1", "subject": {}})


def test_validate_input_ofac_reject_ok():
    data = {
        "case_id": "OFAC-SYN-001",
        "report_type_code": "OFAC_REJECT",
        "transaction": {"amount_rejected": "18450.00", "currency": "USD"},
        "case_facts": {"disposition": "Rejected — transaction not processed"},
    }
    assert validate_input(data, report_type_code="OFAC_REJECT") == data


def test_validate_input_ofac_reject_missing_transaction():
    with pytest.raises(ValueError, match="transaction"):
        validate_input(
            {"case_id": "OFAC-1", "case_facts": {}},
            report_type_code="OFAC_REJECT",
        )


def test_validate_output_ok():
    out = validate_output({"narrative": "Factual narrative text."})
    assert out.narrative == "Factual narrative text."


def test_validate_output_extra_keys():
    out = validate_output({"narrative": "Text.", "extra": "ignored"})
    assert out.narrative == "Text."
