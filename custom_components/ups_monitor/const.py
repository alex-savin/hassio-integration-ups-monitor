from homeassistant.const import Platform

DOMAIN = "ups_monitor"
PLATFORMS = [Platform.SENSOR, Platform.BUTTON]
DEFAULT_SERVER_URL = "ws://homeassistant.local:8080/ws"
DEFAULT_UPDATE_INTERVAL = 10
SUPPORTED_TYPES = ["apcupsd", "nut"]
UPDATE_SIGNAL = "ups_monitor_update"
RECONNECT_DELAY = 5
