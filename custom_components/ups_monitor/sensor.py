"""Sensors for UPS Monitor websocket integration."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricPotential,
    UnitOfElectricCurrent,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfFrequency,
)

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN, UPDATE_SIGNAL
from .device_info import build_device_info
from .helpers import get_ups_status, normalize_attribute_value


_LOGGER = logging.getLogger(__name__)


ATTRIBUTE_META: dict[str, dict[str, Optional[str]]] = {
    "battery_charge": {
        "icon": "mdi:battery",
        "unit": PERCENTAGE,
        "device_class": "battery",
    },
    "time_left": {
        "icon": "mdi:clock-outline",
        "unit": UnitOfTime.MINUTES,
        "device_class": None,
    },
    "input_voltage": {
        "icon": "mdi:flash",
        "unit": UnitOfElectricPotential.VOLT,
        "device_class": "voltage",
    },
    "output_voltage": {
        "icon": "mdi:flash",
        "unit": UnitOfElectricPotential.VOLT,
        "device_class": "voltage",
    },
    "battery_voltage": {
        "icon": "mdi:battery-charging",
        "unit": UnitOfElectricPotential.VOLT,
        "device_class": "voltage",
    },
    "internal_temperature": {
        "icon": "mdi:thermometer",
        "unit": UnitOfTemperature.CELSIUS,
        "device_class": "temperature",
    },
    "load_percentage": {"icon": "mdi:gauge", "unit": PERCENTAGE, "device_class": None},
    "real_power": {
        "icon": "mdi:lightning-bolt",
        "unit": UnitOfPower.WATT,
        "device_class": "power",
    },
    "input_frequency": {
        "icon": "mdi:sine-wave",
        "unit": UnitOfFrequency.HERTZ,
        "device_class": None,
    },
    "output_frequency": {
        "icon": "mdi:sine-wave",
        "unit": UnitOfFrequency.HERTZ,
        "device_class": None,
    },
    "status": {"icon": "mdi:power-plug", "unit": None, "device_class": None},
    "battery_current": {
        "icon": "mdi:current-dc",
        "unit": UnitOfElectricCurrent.AMPERE,
        "device_class": "current",
    },
    "time_on_battery": {
        "icon": "mdi:timer",
        "unit": UnitOfTime.SECONDS,
        "device_class": None,
    },
    "number_transfers": {
        "icon": "mdi:arrow-expand-vertical",
        "unit": "time(s)",
        "device_class": None,
    },
    "model": {"icon": "mdi:tag-text-outline", "unit": None, "device_class": None},
    "ups_model": {"icon": "mdi:tag-text-outline", "unit": None, "device_class": None},
    "device_model": {
        "icon": "mdi:tag-text-outline",
        "unit": None,
        "device_class": None,
    },
    "serial_number": {"icon": "mdi:barcode", "unit": None, "device_class": None},
    "ups_serial": {"icon": "mdi:barcode", "unit": None, "device_class": None},
    "serialno": {"icon": "mdi:barcode", "unit": None, "device_class": None},
    "device_serial": {"icon": "mdi:barcode", "unit": None, "device_class": None},
    "xon_battery": {"icon": "mdi:battery-alert", "unit": None, "device_class": None},
    "last_transfer": {"icon": "mdi:calendar-sync", "unit": None, "device_class": None},
    "cumulative_time_on_battery": {
        "icon": "mdi:chart-bell-curve-cumulative",
        "unit": UnitOfTime.SECONDS,
        "device_class": None,
    },
}

# Attributes that should be marked as diagnostic
DIAGNOSTIC_ATTRIBUTES = {
    "manufacturer",
    "ups_mfr",
    "mfr",
    "model",
    "ups_model",
    "device_model",
    "serial_number",
    "ups_serial",
    "serialno",
    "device_serial",
}


def _display_name(attribute: str) -> str:
    """Return human-readable display name for an attribute."""
    words = attribute.replace("_", " ").split()
    return " ".join(w.upper() if w.lower() == "ups" else w.capitalize() for w in words)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up UPS Monitor sensors from config entry."""
    added: set[str] = set()

    # Use entry-specific signal to avoid cross-entry interference
    update_signal = f"{UPDATE_SIGNAL}_{entry.entry_id}"

    def _maybe_add_entities() -> None:
        """Add entities for any new devices/attributes discovered."""
        store: Dict[str, Any] = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        devices = store.get("devices", {})
        configured_names = store.get("configured_names") or set()

        _LOGGER.debug(
            "Entity discovery: devices=%s configured_names=%s already_added=%d",
            list(devices.keys()),
            configured_names,
            len(added),
        )

        new_entities: List[SensorEntity] = []

        for device_name, device in devices.items():
            # Filter to configured devices if any are configured
            if configured_names and device_name not in configured_names:
                _LOGGER.debug(
                    "Skipping device %s (not in configured_names)", device_name
                )
                continue

            # Status sensor
            status_key = f"{device_name}-status"
            if status_key not in added:
                added.add(status_key)
                new_entities.append(
                    UPSStatusSensor(hass, entry.entry_id, device_name, update_signal)
                )

            # Attribute sensors
            attrs = device.get("attributes") or {}
            for attr_key in sorted(attrs.keys()):
                entity_key = f"{device_name}-{attr_key}"
                if entity_key in added:
                    continue
                if attr_key == "status":
                    continue  # Already handled by status sensor
                added.add(entity_key)
                new_entities.append(
                    UPSAttributeSensor(
                        hass, entry.entry_id, device_name, attr_key, update_signal
                    )
                )

        if new_entities:
            _LOGGER.info("Adding %d new sensor entities", len(new_entities))
            async_add_entities(new_entities)
        else:
            _LOGGER.debug("No new sensor entities to add")

    # Initial add
    _maybe_add_entities()

    # Subscribe to dispatcher for future updates and store unsubscribe for cleanup
    entry.async_on_unload(
        async_dispatcher_connect(hass, update_signal, _maybe_add_entities)
    )


class UPSAttributeSensor(SensorEntity):
    """Sensor for a UPS attribute."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        device_name: str,
        attribute: str,
        update_signal: str,
    ) -> None:
        """Initialize the sensor."""
        self._hass = hass
        self._entry_id = entry_id
        self._device_name = device_name
        self._attribute = attribute
        self._update_signal = update_signal
        self._attr_unique_id = f"{entry_id}-{device_name}-{attribute}"
        self._attr_name = _display_name(attribute)

        meta = ATTRIBUTE_META.get(attribute, {})
        self._attr_icon = meta.get("icon")
        self._attr_native_unit_of_measurement = meta.get("unit")
        self._attr_device_class = meta.get("device_class")

        if attribute in DIAGNOSTIC_ATTRIBUTES:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    def _get_device(self) -> Dict[str, Any] | None:
        """Get device data from store."""
        store = self._hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        return store.get("devices", {}).get(self._device_name)

    async def async_added_to_hass(self) -> None:
        """Subscribe to dispatcher when added."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._update_signal, self._handle_update
            )
        )
        self._handle_update()

    def _handle_update(self) -> None:
        """Handle dispatcher update."""
        device = self._get_device()
        if device is None:
            self._attr_available = False
            self.hass.add_job(self.async_write_ha_state)
            return

        self._attr_available = True
        attrs = device.get("attributes") or {}
        raw_val = attrs.get(self._attribute)
        self._attr_native_value = normalize_attribute_value(self._attribute, raw_val)
        self.hass.add_job(self.async_write_ha_state)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        device = self._get_device() or {}
        return build_device_info(self._device_name, device)


class UPSStatusSensor(SensorEntity):
    """Sensor for UPS status (online/on_battery)."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self, hass: HomeAssistant, entry_id: str, device_name: str, update_signal: str
    ) -> None:
        """Initialize the sensor."""
        self._hass = hass
        self._entry_id = entry_id
        self._device_name = device_name
        self._update_signal = update_signal
        self._attr_unique_id = f"{entry_id}-{device_name}-status"
        self._attr_name = "Status"
        self._attr_icon = "mdi:power-plug"

    def _get_device(self) -> Dict[str, Any] | None:
        """Get device data from store."""
        store = self._hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        return store.get("devices", {}).get(self._device_name)

    async def async_added_to_hass(self) -> None:
        """Subscribe to dispatcher when added."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._update_signal, self._handle_update
            )
        )
        self._handle_update()

    def _handle_update(self) -> None:
        """Handle dispatcher update."""
        device = self._get_device()
        self._attr_available = device is not None
        self._attr_native_value = get_ups_status(device)
        self.hass.add_job(self.async_write_ha_state)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        device = self._get_device() or {}
        return build_device_info(self._device_name, device)
