"""UPS Monitor websocket integration with push-updated entities."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
import aiohttp
import async_timeout

from .const import DOMAIN, PLATFORMS, RECONNECT_DELAY, UPDATE_SIGNAL
from .helpers import build_http_url, HTTP_TIMEOUT

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _update_state(hass: HomeAssistant, entry_id: str, payload: str) -> None:
    """Parse incoming WS payload and update hass.data store."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        _LOGGER.debug("Dropping non-JSON payload: %s", payload[:200])
        return

    # The go-ups server sends a list of device objects
    devices_list = data if isinstance(data, list) else []

    store: Dict[str, Any] = hass.data.setdefault(DOMAIN, {}).setdefault(entry_id, {})
    devices_dict = store.setdefault("devices", {})

    updated_names = []
    for device in devices_list:
        name = device.get("device_name")
        if not name:
            continue
        devices_dict[name] = device
        updated_names.append(name)

    if updated_names:
        _LOGGER.debug("Updated devices from websocket: %s", updated_names)
    else:
        _LOGGER.debug(
            "Websocket update had no devices; keys=%s",
            list(data.keys()) if isinstance(data, dict) else "list",
        )

    # Use entry-specific signal
    async_dispatcher_send(hass, f"{UPDATE_SIGNAL}_{entry_id}")


async def _listen_ws(
    hass: HomeAssistant, entry_id: str, ws_url: str, stop_event: asyncio.Event
) -> None:
    """Maintain websocket connection to go-ups server with auto-reconnect."""
    import websockets

    while not stop_event.is_set():
        try:
            async with websockets.connect(ws_url) as websocket:
                _LOGGER.info("Connected to UPS websocket: %s", ws_url)
                async for message in websocket:
                    _update_state(hass, entry_id, message)
        except asyncio.CancelledError:
            raise
        except websockets.exceptions.ConnectionClosedOK as err:
            _LOGGER.info(
                "Websocket closed cleanly; retrying in %ss (code=%s, reason=%s)",
                RECONNECT_DELAY,
                err.code,
                err.reason,
            )
        except websockets.exceptions.ConnectionClosedError as err:
            _LOGGER.warning(
                "Websocket connection closed unexpectedly; retrying in %ss (code=%s, reason=%s)",
                RECONNECT_DELAY,
                err.code,
                err.reason,
            )
        except Exception as err:
            _LOGGER.warning(
                "Websocket connection dropped; retrying in %ss: %s",
                RECONNECT_DELAY,
                err,
            )

        if not stop_event.is_set():
            await asyncio.sleep(RECONNECT_DELAY)

    _LOGGER.info("Websocket listener stopped")


async def _fetch_initial_status(ws_url: str) -> list:
    """Fetch initial device status via HTTP to seed entities before WS connects."""
    status_url = build_http_url(ws_url, "/api/status")
    if not status_url:
        return []
    try:
        async with aiohttp.ClientSession() as session:
            async with async_timeout.timeout(HTTP_TIMEOUT):
                async with session.get(status_url) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    _LOGGER.debug("Initial HTTP fetch returned status %d", resp.status)
    except asyncio.TimeoutError:
        _LOGGER.debug("Initial HTTP fetch timed out (will rely on WS)")
    except Exception as err:
        _LOGGER.debug("Initial HTTP fetch failed (will rely on WS): %s", err)
    return []


def _get_configured_device_names(entry: ConfigEntry) -> set[str]:
    """Get set of configured device names from entry options."""
    names = set()
    for dev in entry.options.get("devices", []):
        if name := dev.get("device_name"):
            names.add(name)
    # Fallback to legacy single device
    if not names and (legacy := entry.data.get("device_name")):
        names.add(legacy)
    return names


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up UPS Monitor from YAML (not supported, use config flow)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up UPS Monitor from a config entry."""
    ws_url: str = entry.data.get("server_url", "")
    if not ws_url:
        raise ConfigEntryNotReady("No server_url configured")

    # Initialize store
    store: Dict[str, Any] = hass.data.setdefault(DOMAIN, {}).setdefault(
        entry.entry_id, {}
    )
    store["devices"] = {}
    configured_names = _get_configured_device_names(entry)
    store["configured_names"] = configured_names

    _LOGGER.debug(
        "Setting up entry: id=%s server_url=%s configured_devices=%s",
        entry.entry_id,
        ws_url,
        configured_names,
    )

    # Seed with initial HTTP fetch so entities are available immediately
    # Retry a few times to ensure newly added devices are available
    base_http = build_http_url(ws_url, "")
    initial_data = None
    max_retries = 3

    for attempt in range(max_retries):
        initial_data = await _fetch_initial_status(ws_url)
        if initial_data:
            # Check if all configured devices are present in the response
            device_names_in_data = {
                d.get("device_name") for d in initial_data if d.get("device_name")
            }
            missing_devices = configured_names - device_names_in_data
            if not missing_devices:
                break
            _LOGGER.debug(
                "Attempt %d: Missing configured devices in server response: %s, retrying...",
                attempt + 1,
                missing_devices,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(1.0)
        else:
            _LOGGER.debug("Attempt %d: No data from server, retrying...", attempt + 1)
            if attempt < max_retries - 1:
                await asyncio.sleep(1.0)

    if initial_data:
        _update_state(hass, entry.entry_id, json.dumps(initial_data))
        _LOGGER.debug("Seeded %d devices from HTTP", len(initial_data))

    # Start websocket listener
    stop_event = asyncio.Event()
    task = hass.loop.create_task(_listen_ws(hass, entry.entry_id, ws_url, stop_event))

    store["runtime"] = {
        "stop_event": stop_event,
        "task": task,
        "base_http": base_http,
    }

    async def _stop_ws(event: Any) -> None:
        stop_event.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop_ws)

    # Listen for options updates to refresh configured devices
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # Forward to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info("UPS Monitor websocket listener started: %s", ws_url)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update - reload the integration to rediscover entities."""
    _LOGGER.info(
        "Options updated, scheduling integration reload for entry %s", entry.entry_id
    )

    # Give server time to register any new device and start monitoring
    # before we reload and fetch fresh data
    await asyncio.sleep(2.0)

    # Reload the entire integration - this will:
    # 1. Unload platforms and stop websocket
    # 2. Re-setup with fresh HTTP fetch
    # 3. Re-discover all entities based on new options and server data
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    store = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    runtime = store.get("runtime")
    if runtime:
        runtime["stop_event"].set()
        runtime["task"].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime["task"]

    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
