"""Button entities for UPS remote commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List
import logging

import async_timeout
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, UPDATE_SIGNAL
from .device_info import build_device_info
from .helpers import HTTP_TIMEOUT

_LOGGER = logging.getLogger(__name__)


@dataclass
class ButtonDescription:
    """Describes a UPS command button."""

    key: str
    name: str
    icon: str
    command: str
    device_types: tuple[str, ...] = ("nut", "apcupsd")


# Common UPS commands supported by NUT
BUTTON_DESCRIPTIONS: List[ButtonDescription] = [
    ButtonDescription(
        key="beeper_enable",
        name="Enable Beeper",
        icon="mdi:volume-high",
        command="beeper.enable",
        device_types=("nut",),
    ),
    ButtonDescription(
        key="beeper_disable",
        name="Disable Beeper",
        icon="mdi:volume-off",
        command="beeper.disable",
        device_types=("nut",),
    ),
    ButtonDescription(
        key="beeper_mute",
        name="Mute Beeper",
        icon="mdi:volume-mute",
        command="beeper.mute",
        device_types=("nut",),
    ),
    ButtonDescription(
        key="test_battery_start",
        name="Start Battery Test",
        icon="mdi:battery-sync",
        command="test.battery.start",
        device_types=("nut",),
    ),
    ButtonDescription(
        key="test_battery_stop",
        name="Stop Battery Test",
        icon="mdi:battery-check",
        command="test.battery.stop",
        device_types=("nut",),
    ),
    ButtonDescription(
        key="load_off",
        name="Turn Off Load",
        icon="mdi:power-plug-off",
        command="load.off",
        device_types=("nut",),
    ),
    ButtonDescription(
        key="load_on",
        name="Turn On Load",
        icon="mdi:power-plug",
        command="load.on",
        device_types=("nut",),
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up UPS button entities from config entry."""
    added: set[str] = set()

    # Use entry-specific signal to avoid cross-entry interference
    update_signal = f"{UPDATE_SIGNAL}_{entry.entry_id}"

    def _maybe_add_entities() -> None:
        new_entities: list[UPSCommandButton] = []
        store: Dict[str, Any] = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        devices = store.get("devices", {})
        configured_names = store.get("configured_names") or set()

        _LOGGER.debug(
            "Button entity discovery: devices=%s configured_names=%s",
            list(devices.keys()),
            configured_names,
        )

        for device_name, device in devices.items():
            # Filter to configured devices if any are configured
            if configured_names and device_name not in configured_names:
                continue

            device_type = device.get("type", "nut")

            for desc in BUTTON_DESCRIPTIONS:
                # Only add buttons for supported device types
                if device_type not in desc.device_types:
                    continue

                key = f"{entry.entry_id}-{device_name}-{desc.key}"
                if key in added:
                    continue
                added.add(key)
                new_entities.append(
                    UPSCommandButton(
                        hass, entry.entry_id, device_name, desc, update_signal
                    )
                )

        if new_entities:
            _LOGGER.info("Adding %d new button entities", len(new_entities))
            async_add_entities(new_entities)

    # Initial add
    _maybe_add_entities()

    # Subscribe to dispatcher for future updates and store unsubscribe for cleanup
    entry.async_on_unload(
        async_dispatcher_connect(hass, update_signal, _maybe_add_entities)
    )


class UPSCommandButton(ButtonEntity):
    """Button entity for UPS commands."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        device_name: str,
        description: ButtonDescription,
        update_signal: str,
    ) -> None:
        """Initialize the button."""
        self._hass = hass
        self._entry_id = entry_id
        self._device_name = device_name
        self._description = description
        self._update_signal = update_signal
        self._attr_unique_id = f"{entry_id}-{device_name}-{description.key}"
        self._attr_name = description.name
        self._attr_icon = description.icon
        self._last_result: Dict[str, Any] | None = None

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
        self.hass.add_job(self.async_write_ha_state)

    async def async_press(self) -> None:
        """Execute the UPS command."""
        store = self._hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        runtime = store.get("runtime", {})
        base_http: str | None = runtime.get("base_http")

        if not base_http:
            raise HomeAssistantError("UPS server base URL is unavailable")

        # Build command URL - the Go server expects POST to /api/device with command
        url = f"{base_http}/api/command"
        payload = {
            "device": self._device_name,
            "command": self._description.command,
        }

        session = async_get_clientsession(self.hass)
        try:
            async with async_timeout.timeout(HTTP_TIMEOUT):
                resp = await session.post(url, json=payload)
                result = await resp.json()

            if resp.status >= 400:
                message = result.get("error", "unknown error")
                self._last_result = {"success": False, "error": message}
                raise HomeAssistantError(
                    f"Command failed ({resp.status}) for {self.name}: {message}"
                )

            self._last_result = result
            _LOGGER.info(
                "UPS command executed: device=%s command=%s success=%s",
                self._device_name,
                self._description.command,
                result.get("success", False),
            )

        except HomeAssistantError:
            raise
        except Exception as err:
            self._last_result = {"success": False, "error": str(err)}
            raise HomeAssistantError(f"Command failed for {self.name}: {err}") from err

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes."""
        attrs: Dict[str, Any] = {
            "device_name": self._device_name,
            "command": self._description.command,
        }
        if self._last_result:
            attrs["last_result"] = self._last_result
        return attrs

    @property
    def device_info(self):
        """Return device info."""
        device = self._get_device() or {}
        return build_device_info(self._device_name, device)
