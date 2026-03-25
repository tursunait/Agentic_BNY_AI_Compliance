"""Unit tests for PDF filler mapping (no Supabase/network)."""

from backend.pdf_filler.fill_engine import (
    _get_nested,
    _mapping_entries,
    _resolve_pdf_field_name,
    _resolve_source_value,
    build_field_values,
)
from backend.pdf_filler.metadata import normalize_report_type_code
from backend.pdf_filler.sar_flatten import flatten_sar_case, maybe_flatten_sar_transaction_json
from backend.pdf_filler.sar_mapping import is_sar_new_mapping, parse_sar_mapping
from backend.pdf_filler.fill_engine import build_sar_field_values


def test_normalize_report_type():
    assert normalize_report_type_code("SANCTIONS_REJECT") == "OFAC_REJECT"
    assert normalize_report_type_code("SAR") == "SAR"


def test_resolve_pdf_field_name():
    assert _resolve_pdf_field_name("Amount Rejected") == "Amount Rejected"
    assert _resolve_pdf_field_name({"field_id": "Zip", "type": "text"}) == "Zip"
    assert _resolve_pdf_field_name(None) is None


def test_get_nested():
    d = {"a": {"b": {"c": 1}}}
    assert _get_nested(d, "a.b.c") == 1
    assert _get_nested(d, "a.missing") is None


def test_narrative_resolution():
    data = {"narrative": "from case"}
    assert _resolve_source_value("narrative_text", data, "override") == "override"
    assert _resolve_source_value("narrative", data, None) == "from case"


def test_build_field_values_simple_mapping():
    mapping = {
        "foo": "FieldA",
        "narrative": "NarrField",
    }
    pdf_fields = {"FieldA", "NarrField"}
    data = {"foo": "x", "narrative": "long story"}
    u = build_field_values(mapping, data, None, pdf_fields)
    assert u["FieldA"] == "x"
    assert u["NarrField"] == "long story"


def test_sar_flatten_from_nested():
    case = {
        "report_type": "SAR",
        "subject": {"name": "Acme LLC", "type": "Entity", "country": "US", "industry_or_occupation": "Retail"},
        "institution": {"name": "Test Bank", "branch_city": "NYC", "branch_state": "NY"},
        "alert": {"alert_id": "A-1"},
        "SuspiciousActivityInformation": {
            "26_AmountInvolved": {"amount_usd": 1000.0, "no_amount": False},
            "27_DateOrDateRange": {"from": "01/01/2024", "to": "01/02/2024"},
        },
        "narrative": "Test narrative.",
    }
    flat = flatten_sar_case(case)
    assert flat["3"] == "Acme LLC"
    assert flat["26"] == "1000.0"
    assert flat["27a"] == "01/01/2024"
    assert flat["narrative_text"] == "Test narrative."
    merged = maybe_flatten_sar_transaction_json(case)
    assert merged["53"] == "Test Bank"


def test_mapping_entries_rich_format():
    m = {
        "transaction.amount": {"type": "text", "field_id": "Amt"},
        "narrative": {"field_id": "Page 2"},
    }
    pairs = dict(_mapping_entries(m))
    assert pairs["transaction.amount"] == "Amt"
    assert pairs["narrative"] == "Page 2"


def test_sar_new_mapping_detection():
    old = {"3": "3  Last name", "narrative_text": "Narrative"}
    assert not is_sar_new_mapping(old)
    new = {
        "part_I_subject_information": {
            "a": {"type": "checkbox", "label": "1a - Initial report"},
            "3  Individuals last name or entitys legal name a Unk": {"type": "text", "label": "3 - Last name or entity legal name"},
        }
    }
    assert is_sar_new_mapping(new)


def test_parse_sar_mapping():
    m = {
        "part_I_type_of_filing": {
            "a": {"type": "checkbox", "label": "1a - Initial report"},
            "Text2": {"type": "text", "label": "1e - Prior document number"},
        },
        "part_I_subject_information": {
            "3  Individuals last name or entitys legal name a Unk": {"type": "text", "label": "3 - Last name or entity legal name"},
        },
    }
    flat_specs, data_key_to_pdf, checkboxes = parse_sar_mapping(m)
    assert "a" in checkboxes
    assert "3  Individuals last name or entitys legal name a Unk" not in checkboxes
    assert data_key_to_pdf.get("1a") == "a"
    assert data_key_to_pdf.get("3") == "3  Individuals last name or entitys legal name a Unk"
    assert flat_specs["a"]["type"] == "checkbox"


def test_build_sar_field_values():
    mapping = {
        "part_I_subject_information": {
            "3  Individuals last name or entitys legal name a Unk": {"type": "text", "label": "3 - Last name or entity legal name"},
            "a": {"type": "checkbox", "label": "1a - Initial"},
        },
    }
    data = {"3": "Global Trade Corp", "1a": "Yes", "narrative_text": "Narrative here."}
    pdf_names = {"3  Individuals last name or entitys legal name a Unk", "a", "Narrative"}
    updates = build_sar_field_values(mapping, data, None, pdf_names, fields=None)
    assert "3  Individuals last name or entitys legal name a Unk" in updates
    assert updates["3  Individuals last name or entitys legal name a Unk"] == "Global Trade Corp"
    assert "a" in updates
    assert updates["a"] == "/Yes"
    assert "Narrative" in updates
    assert updates["Narrative"] == "Narrative here."
