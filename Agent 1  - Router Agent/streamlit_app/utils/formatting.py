"""Formatting helpers used across pages and components."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def format_datetime(dt_string: Any, fmt: str = "default") -> str:
    if not dt_string:
        return "-"
    text = str(dt_string)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
            try:
                dt = datetime.strptime(text, pattern)
                break
            except ValueError:
                dt = None
        if dt is None:
            return text

    if fmt == "short":
        return dt.strftime("%b %d, %Y")
    if fmt == "long":
        return dt.strftime("%B %d, %Y at %I:%M %p")
    return dt.strftime("%b %d, %Y %I:%M %p")


def format_currency(amount: Any, currency: str = "USD") -> str:
    try:
        value = float(amount)
    except (TypeError, ValueError):
        value = 0.0
    symbol = "$" if currency.upper() == "USD" else f"{currency.upper()} "
    return f"{symbol}{value:,.2f}"


def format_duration(seconds: Any) -> str:
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "-"
    if value < 60:
        return f"{value:.1f}s"
    if value < 3600:
        return f"{value / 60:.1f}m"
    return f"{value / 3600:.1f}h"
