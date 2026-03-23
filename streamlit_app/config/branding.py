"""Branding constants for BNY Mellon themed UI."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BNYColors:
    BNY_BLUE: str = "#003B5C"
    BNY_LIGHT_BLUE: str = "#0077C8"
    BNY_TEAL: str = "#00A3B5"

    CHARCOAL: str = "#2C2C2C"
    GRAY_DARK: str = "#4A4A4A"
    GRAY_MEDIUM: str = "#767676"
    GRAY_LIGHT: str = "#D3D3D3"
    GRAY_LIGHTEST: str = "#F5F5F5"
    WHITE: str = "#FFFFFF"

    SUCCESS: str = "#28A745"
    WARNING: str = "#FFC107"
    DANGER: str = "#DC3545"
    INFO: str = "#17A2B8"

    SAR_COLOR: str = "#E74C3C"
    CTR_COLOR: str = "#3498DB"
    SANCTIONS_COLOR: str = "#9B59B6"

    RISK_HIGH: str = "#C0392B"
    RISK_MEDIUM: str = "#E67E22"
    RISK_LOW: str = "#F39C12"
    RISK_NONE: str = "#27AE60"


@dataclass(frozen=True)
class Spacing:
    XS: str = "0.25rem"
    SM: str = "0.5rem"
    MD: str = "1rem"
    LG: str = "1.5rem"
    XL: str = "2rem"
    XXL: str = "3rem"
