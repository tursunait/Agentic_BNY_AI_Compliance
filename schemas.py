"""Input and output schemas for SAR narrative generation."""

from typing import Any

from pydantic import BaseModel, Field


class NarrativeOutput(BaseModel):
    """Output schema: generated narrative in JSON format."""

    narrative: str = Field(
        ...,
        description="The generated SAR narrative section based strictly on the provided input data.",
    )


def validate_input(data: dict[str, Any]) -> dict[str, Any]:
    """
    Validate that input has required keys for narrative generation.
    Does not enforce full structure so various payload shapes are accepted.
    """
    required = {"case_id", "subject", "SuspiciousActivityInformation"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"Input missing required keys: {missing}")
    return data


def validate_output(data: dict[str, Any]) -> NarrativeOutput:
    """Validate and parse narrative output."""
    return NarrativeOutput(**data)
