import aiohttp
import asyncio
import logging
from typing import List
from urllib.parse import quote, urlparse

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import DEFAULT_SERVER_URL, DEFAULT_UPDATE_INTERVAL, DOMAIN, SUPPORTED_TYPES
from .helpers import build_http_url

_LOGGER = logging.getLogger(__name__)


SERVER_SCHEMA = vol.Schema(
    {
        vol.Required("server_url", default=DEFAULT_SERVER_URL): cv.string,
        vol.Optional("update_interval", default=DEFAULT_UPDATE_INTERVAL): vol.All(
            cv.positive_int, vol.Range(min=5)
        ),
    }
)


# Schema for device connection info (step 1)
DEVICE_CONNECTION_SCHEMA = vol.Schema(
    {
        vol.Required("device_name"): str,
        vol.Required("device_type", default=SUPPORTED_TYPES[0]): vol.In(
            SUPPORTED_TYPES
        ),
        vol.Required("device_host"): str,
        vol.Required("device_port", default=3551): int,
        vol.Optional("username", default=""): str,
        vol.Optional("password", default=""): str,
    }
)

# Legacy schema with text input for attributes (fallback)
DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required("device_name"): str,
        vol.Required("device_type", default=SUPPORTED_TYPES[0]): vol.In(
            SUPPORTED_TYPES
        ),
        vol.Required("device_host"): str,
        vol.Required("device_port", default=3551): int,
        vol.Optional("username", default=""): str,
        vol.Optional("password", default=""): str,
        vol.Optional("selected_attributes", default=""): cv.string,
    }
)


class UPSMonitorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return UPSMonitorOptionsFlow(config_entry)

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}
        if user_input is not None:
            server_url = user_input["server_url"]
            parsed = urlparse(server_url)
            if parsed.scheme not in ("ws", "wss", "http", "https") or not parsed.netloc:
                errors["server_url"] = "invalid_url"
            else:
                unique = server_url
                await self.async_set_unique_id(unique)
                self._abort_if_unique_id_configured()

                if not await self._async_validate_server(server_url):
                    errors["base"] = "cannot_connect"
                else:
                    title = f"UPS Monitor ({server_url})"
                    return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=SERVER_SCHEMA,
            errors=errors,
        )

    async def _async_validate_server(self, server_url: str) -> bool:
        url = build_http_url(server_url, "/health")
        if not url:
            return False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    return resp.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False


class UPSMonitorOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._device_to_reconfigure: dict | None = None
        # For multi-step add device flow
        self._pending_device: dict | None = None
        self._available_attributes: List[str] = []

    @property
    def config_entry(self) -> config_entries.ConfigEntry:  # type: ignore[override]
        return self._entry

    async def async_step_init(self, user_input=None) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add_device",
                "remove_device",
                "reconfigure_device",
                "set_interval",
            ],
        )

    async def async_step_add_device(self, user_input=None) -> FlowResult:
        """Step 1: Collect device connection info and validate."""
        errors = {}
        if user_input is not None:
            try:
                devices = list(self.config_entry.options.get("devices", []))
                if any(
                    d.get("device_name") == user_input.get("device_name")
                    for d in devices
                ):
                    errors["device_name"] = "already_configured"
                else:
                    # Test connection and fetch available attributes
                    attributes = await self._async_test_device_and_get_attributes(
                        user_input
                    )
                    if attributes is None:
                        errors["base"] = "cannot_connect"
                    else:
                        # Store pending device and move to attribute selection
                        self._pending_device = user_input
                        self._available_attributes = sorted(attributes)
                        return await self.async_step_select_attributes()
            except Exception as err:  # noqa: BLE001
                _LOGGER.exception("Unexpected error testing device: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="add_device",
            data_schema=DEVICE_CONNECTION_SCHEMA,
            errors=errors,
            description_placeholders={
                "step_info": "Enter device connection details. We'll test the connection and show available attributes.",
            },
        )

    async def async_step_select_attributes(self, user_input=None) -> FlowResult:
        """Step 2: Let user select which attributes to monitor."""
        if self._pending_device is None:
            return self.async_abort(reason="no_pending_device")

        errors = {}
        if user_input is not None:
            try:
                # Combine connection info with selected attributes
                device_config = {
                    **self._pending_device,
                    "selected_attributes": ",".join(
                        user_input.get("selected_attributes", [])
                    ),
                }

                # Register device with server
                if not await self._async_register_device(device_config):
                    errors["base"] = "cannot_connect"
                else:
                    devices = list(self.config_entry.options.get("devices", []))
                    devices.append(device_config)

                    # Clear pending state
                    self._pending_device = None
                    self._available_attributes = []

                    # Note: async_create_entry triggers _async_options_updated
                    # which will handle entity discovery after fetching server data
                    return self.async_create_entry(
                        title="Added device",
                        data={
                            "devices": devices,
                            "update_interval": self._get_update_interval(),
                        },
                    )
            except Exception as err:  # noqa: BLE001
                _LOGGER.exception("Unexpected error adding device: %s", err)
                errors["base"] = "unknown"

        # Build attribute selection schema
        if self._available_attributes:
            # Create options for multi-select
            attr_options = [
                {"value": attr, "label": self._format_attribute_label(attr)}
                for attr in self._available_attributes
            ]
            schema = vol.Schema(
                {
                    vol.Optional(
                        "selected_attributes", default=self._available_attributes
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=attr_options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            )
        else:
            # No attributes available - just confirm
            schema = vol.Schema({})

        device_name = self._pending_device.get("device_name", "Unknown")
        return self.async_show_form(
            step_id="select_attributes",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "device_name": device_name,
                "attribute_count": str(len(self._available_attributes)),
            },
        )

    def _format_attribute_label(self, attribute: str) -> str:
        """Format attribute name for display."""
        words = attribute.replace("_", " ").split()
        return " ".join(
            w.upper() if w.lower() == "ups" else w.capitalize() for w in words
        )

    async def async_step_reconfigure_device(self, user_input=None) -> FlowResult:
        opt_devices = list(self.config_entry.options.get("devices", []))
        all_devices = opt_devices

        if not all_devices:
            return self.async_abort(reason="no_devices")

        device_names = {d["device_name"] for d in all_devices if "device_name" in d}
        schema = vol.Schema({vol.Required("device_name"): vol.In(sorted(device_names))})

        if user_input is None:
            return self.async_show_form(
                step_id="reconfigure_device", data_schema=schema
            )

        selected = next(
            (
                d
                for d in all_devices
                if d.get("device_name") == user_input["device_name"]
            ),
            None,
        )
        if not selected:
            return self.async_abort(reason="unknown_device")

        self._device_to_reconfigure = selected
        return await self.async_step_reconfigure_device_update()

    async def async_step_reconfigure_device_update(self, user_input=None) -> FlowResult:
        devices = list(self.config_entry.options.get("devices", []))
        all_devices = devices
        if not self._device_to_reconfigure:
            return self.async_abort(reason="unknown_device")

        defaults = dict(self._device_to_reconfigure)
        errors: dict[str, str] = {}
        attr_default = defaults.get("selected_attributes") or ""
        if isinstance(attr_default, list):
            attr_default = ",".join(attr_default)

        schema = vol.Schema(
            {
                vol.Required("device_name", default=defaults.get("device_name")): str,
                vol.Required(
                    "device_type",
                    default=defaults.get("device_type", SUPPORTED_TYPES[0]),
                ): vol.In(SUPPORTED_TYPES),
                vol.Required(
                    "device_host", default=defaults.get("device_host", "")
                ): str,
                vol.Required(
                    "device_port", default=defaults.get("device_port", 3551)
                ): int,
                vol.Optional("username", default=defaults.get("username", "")): str,
                vol.Optional("password", default=defaults.get("password", "")): str,
                vol.Optional("selected_attributes", default=attr_default): cv.string,
            }
        )

        if user_input is not None:
            new_name = user_input.get("device_name")
            # Prevent duplicates when renaming across configured devices
            if new_name != defaults.get("device_name") and any(
                d.get("device_name") == new_name for d in all_devices
            ):
                errors["device_name"] = "already_configured"
            elif not await self._async_register_device(user_input):
                errors["base"] = "cannot_connect"
            else:
                old_name = defaults.get("device_name")
                if old_name and new_name != old_name:
                    if not await self._async_delete_device(old_name):
                        errors["base"] = "cannot_connect"
                        return self.async_show_form(
                            step_id="reconfigure_device_update",
                            data_schema=schema,
                            errors=errors,
                        )
                updated = {
                    **user_input,
                    "selected_attributes": [
                        a.strip()
                        for a in user_input.get("selected_attributes", "").split(",")
                        if a.strip()
                    ],
                }
                devices = [
                    updated
                    if d.get("device_name") == defaults.get("device_name")
                    else d
                    for d in devices
                ]
                result = self.async_create_entry(
                    title="Device updated",
                    data={
                        "devices": devices,
                        "update_interval": self._get_update_interval(),
                    },
                )
                self.hass.bus.async_fire("ups_monitor_devices_changed")
                return result

        self.hass.bus.async_fire("ups_monitor_devices_changed")

        return self.async_show_form(
            step_id="reconfigure_device_update", data_schema=schema, errors=errors
        )

    async def async_step_remove_device(self, user_input=None) -> FlowResult:
        devices = list(self.config_entry.options.get("devices", []))
        all_devices = devices
        if not all_devices:
            return self.async_abort(reason="no_devices")

        device_names = {d["device_name"] for d in all_devices if "device_name" in d}
        schema = vol.Schema({vol.Required("device_name"): vol.In(sorted(device_names))})
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input["device_name"]
            if not await self._async_delete_device(name):
                errors["base"] = "cannot_connect"
                return self.async_show_form(
                    step_id="remove_device", data_schema=schema, errors=errors
                )
            devices = [d for d in devices if d.get("device_name") != name]

            # Notify that devices changed after successful deletion
            self.hass.bus.async_fire("ups_monitor_devices_changed")

            return self.async_create_entry(
                title="Removed device",
                data={
                    "devices": devices,
                    "update_interval": self._get_update_interval(),
                },
            )

        return self.async_show_form(step_id="remove_device", data_schema=schema)

    async def async_step_set_interval(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}
        current = self._get_update_interval()
        schema = vol.Schema(
            {
                vol.Required("update_interval", default=current): vol.All(
                    cv.positive_int, vol.Range(min=5)
                )
            }
        )

        if user_input is not None:
            interval = user_input.get("update_interval", current)
            return self.async_create_entry(
                title="Update interval set",
                data={
                    "devices": list(self.config_entry.options.get("devices", [])),
                    "update_interval": interval,
                },
            )

        return self.async_show_form(
            step_id="set_interval", data_schema=schema, errors=errors
        )

    async def _async_test_device_and_get_attributes(
        self, device_input: dict
    ) -> List[str] | None:
        """Test device connection and return list of available attributes.

        Returns:
            List of attribute names if connection successful, None otherwise.
        """
        server_url = self.config_entry.data.get("server_url")
        url = build_http_url(server_url, "/api/device/test") if server_url else None
        if not url:
            return None

        payload = {
            "type": device_input["device_type"],
            "name": device_input["device_name"],
            "host": device_input["device_host"],
            "port": device_input["device_port"],
            "username": device_input.get("username", ""),
            "password": device_input.get("password", ""),
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.warning(
                            "Device test failed with status %d", resp.status
                        )
                        return None
                    data = await resp.json()
                    # Server returns {"success": true, "attributes": {"key": "value", ...}}
                    if not data.get("success"):
                        _LOGGER.warning(
                            "Device test returned success=false: %s", data.get("error")
                        )
                        return None
                    attributes = data.get("attributes", {})
                    return list(attributes.keys())
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("Device test request failed: %s", err)
            return None

    async def _async_register_device(self, device_input: dict) -> bool:
        server_url = self.config_entry.data.get("server_url")
        url = build_http_url(server_url, "/api/device") if server_url else None
        if not url:
            return False
        raw_attrs = device_input.get("selected_attributes", "")
        selected_attributes = [a.strip() for a in raw_attrs.split(",") if a.strip()]
        payload = {
            "type": device_input["device_type"],
            "name": device_input["device_name"],
            "host": device_input["device_host"],
            "port": device_input["device_port"],
            "username": device_input.get("username", ""),
            "password": device_input.get("password", ""),
            "selected_attributes": selected_attributes,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    return resp.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    async def _async_delete_device(self, device_name: str) -> bool:
        server_url = self.config_entry.data.get("server_url")
        encoded = quote(device_name, safe="")
        url = (
            build_http_url(server_url, f"/api/device?name={encoded}")
            if server_url
            else None
        )
        if not url:
            return False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    url, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    return resp.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    def _get_update_interval(self) -> int:
        return int(
            self.config_entry.options.get(
                "update_interval",
                self.config_entry.data.get("update_interval", DEFAULT_UPDATE_INTERVAL),
            )
        )
