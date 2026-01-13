"""Shared helper functions for UPS Monitor integration."""

from __future__ import annotations

from typing import Any, Dict
from urllib.parse import urlparse, urlunparse

# Default timeout for HTTP requests in seconds
HTTP_TIMEOUT = 10


def build_http_url(server_url: str, path: str) -> str | None:
    """Convert websocket URL to HTTP URL with given path.

    Handles ws:// -> http:// and wss:// -> https:// conversion.
    """
    if not server_url:
        return None

    parsed = urlparse(server_url)
    scheme = parsed.scheme

    if scheme in ("ws", "wss"):
        scheme = "http" if scheme == "ws" else "https"
    elif scheme not in ("http", "https"):
        return None

    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


def get_ups_status(device: Dict[str, Any]) -> str:
    """Determine UPS status from device attributes.

    Returns:
        "on_battery" if UPS is running on battery
        "online" if UPS is on mains power
        "offline" if device data is unavailable
    """
    if not device:
        return "offline"

    attrs = device.get("attributes") or {}
    status_raw = str(attrs.get("status", "")).lower()
    xon = str(attrs.get("xon_battery", "")).lower()

    on_battery = any(
        token in status_raw for token in ("onbatt", "on battery", "ob", "on_battery")
    ) or xon in {"1", "true", "yes"}

    return "On Battery" if on_battery else "Online"


def normalize_attribute_value(attribute: str, value: Any) -> Any:
    """Normalize attribute values for display.

    Handles unit conversions and type coercion.
    """
    if value is None:
        return None

    # Convert time_left from seconds to minutes
    if attribute == "time_left":
        try:
            return round(float(value) / 60, 2)
        except (ValueError, TypeError):
            return value

    # Convert time_on_battery to float
    if attribute == "time_on_battery":
        try:
            return float(value)
        except (ValueError, TypeError):
            return value

    # Numeric attributes that should be floats
    numeric_attrs = {
        "battery_charge",
        "load_percentage",
        "input_voltage",
        "output_voltage",
        "battery_voltage",
        "internal_temperature",
        "real_power",
        "input_frequency",
        "output_frequency",
        "battery_current",
    }
    if attribute in numeric_attrs:
        try:
            return float(value)
        except (ValueError, TypeError):
            return value

    return value
