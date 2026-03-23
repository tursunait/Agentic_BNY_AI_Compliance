"""Input and output schemas for narrative generation (SAR, OFAC_REJECT, etc.)."""

from typing import Any

from pydantic import BaseModel, Field

from narrative_agent.report_types import get_report_type_from_input, get_report_type_spec


class NarrativeOutput(BaseModel):
    """Output schema: generated narrative in JSON format."""

    narrative: str = Field(
        ...,
        description="The generated narrative section based strictly on the provided input data.",
    )


def validate_input(
    data: dict[str, Any],
    report_type_code: str | None = None,
) -> dict[str, Any]:
    """
    Validate that input has required keys for the given report type.
    Does not enforce full structure so various payload shapes are accepted.

    If report_type_code is not provided, it is read from data["report_type_code"] or data["report_type"] (default SAR).
    """
    if report_type_code is None:
        report_type_code = get_report_type_from_input(data)
    spec = get_report_type_spec(report_type_code)
    missing = spec.required_input_keys - set(data.keys())
    if missing:
        raise ValueError(
            f"Input missing required keys for {spec.code}: {missing}. "
            f"Required: {set(spec.required_input_keys)}"
        )
    return data


def validate_output(data: dict[str, Any]) -> NarrativeOutput:
    """Validate and parse narrative output."""
    return NarrativeOutput(**data)
