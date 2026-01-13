"""Helpers for building consistent device info for UPS devices."""

from __future__ import annotations

import re
from typing import Any, Dict

from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN


def build_device_info(device_name: str, device: Dict[str, Any]) -> DeviceInfo:
    """Build DeviceInfo from device payload.

    Extracts manufacturer, model, and serial number from device attributes
    with fallback heuristics for common UPS brands.
    """
    attrs = device.get("attributes") or {}

    # Extract manufacturer
    manufacturer = attrs.get("manufacturer") or attrs.get("ups_mfr") or attrs.get("mfr")

    # Extract model
    model = (
        attrs.get("model")
        or attrs.get("ups_model")
        or attrs.get("device_model")
        or attrs.get("upsmodel")
    )
    if not model:
        model = attrs.get("ups_name") or attrs.get("hostname") or attrs.get("version")

    # Heuristics for CyberPower models (e.g., CP1500PFCLCDa)
    if model:
        m = re.search(r"(CP\d{3,6}[A-Z]*PFCLCD[aA]?)", model, re.IGNORECASE)
        if m:
            model = m.group(1).upper()
            if not manufacturer:
                manufacturer = "CyberPower"
    elif device_name:
        m = re.search(r"(CP\d{3,6}[A-Z]*PFCLCD[aA]?)", device_name, re.IGNORECASE)
        if m:
            model = m.group(1).upper()
            if not manufacturer:
                manufacturer = "CyberPower"

    # Heuristics for APC models
    if not manufacturer and model:
        if re.search(r"(Back-UPS|Smart-UPS|SUA|SMT|SMC|BR\d)", model, re.IGNORECASE):
            manufacturer = "APC"

    # Defaults
    if not model:
        model = device_name
    if not manufacturer:
        manufacturer = "go-ups"

    # Extract serial number
    serial = (
        attrs.get("serial_number")
        or attrs.get("ups_serial")
        or attrs.get("serialno")
        or attrs.get("device_serial")
    )

    return DeviceInfo(
        identifiers={(DOMAIN, device_name)},
        name=device_name,
        manufacturer=manufacturer,
        model=model,
        serial_number=serial,
    )
