# UPS Monitor Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/alex-savin/hassio-integration-ups-monitor)](https://github.com/alex-savin/hassio-integration-ups-monitor/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Custom Home Assistant integration that monitors UPS devices via the `go-ups` websocket service. Supports APC UPS (apcupsd) and NUT-compatible devices.

## Prerequisites

This integration requires the **[UPS Monitor Add-on](https://github.com/alex-savin/hassio-apps/tree/main/ups-monitor)** to be installed and running.

The add-on connects to your UPS devices using apcupsd or NUT protocols and exposes their status via a local websocket. This integration then connects to that websocket to create Home Assistant entities.

### Add-on Installation

1. Add the add-on repository to Home Assistant:
   - Go to **Settings → Add-ons → Add-on Store**
   - Click the three dots (⋮) in the top right → **Repositories**
   - Add: `https://github.com/alex-savin/hassio-apps`
2. Find and install **UPS Monitor**
3. Configure the add-on with your UPS connection details
4. Start the add-on
5. Note the websocket URL (typically `ws://homeassistant.local:8080/ws` or `ws://localhost:8080/ws`)

## Installation

### HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=alex-savin&repository=hassio-integration-ups-monitor&category=integration)

1. Open HACS in Home Assistant
2. Click "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add this repository URL: `https://github.com/alex-savin/hassio-integration-ups-monitor`
6. Select "Integration" as the category
7. Click "Add"
8. Search for "UPS Monitor" and install it
9. Restart Home Assistant

### Manual Installation

1. Download the latest release from the [releases page](https://github.com/alex-savin/hassio-integration-ups-monitor/releases)
2. Copy the `custom_components/ups_monitor` folder to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant
4. Go to **Settings → Devices & Services → Add Integration**
5. Search for "UPS Monitor"
6. Enter the websocket URL of the go-ups add-on and your UPS details (type, name, host, port, optional credentials)

## Supported UPS Types

- **apcupsd** - APC UPS devices via apcupsd daemon
- **nut** - Network UPS Tools (NUT) compatible devices

## Entities

The integration creates the following entities for each configured UPS:

### Sensors
| Sensor | Description | Unit |
|--------|-------------|------|
| Status | Current UPS status (Online, On Battery, etc.) | - |
| Battery Charge | Current battery charge level | % |
| Time Left | Estimated runtime on battery | minutes |
| Input Voltage | Input line voltage | V |
| Output Voltage | Output voltage to load | V |
| Battery Voltage | Current battery voltage | V |
| Load Percentage | Current UPS load | % |
| Real Power | Current power consumption | W |
| Internal Temperature | UPS internal temperature | °C |
| Input Frequency | Input power frequency | Hz |
| Output Frequency | Output power frequency | Hz |
| Time on Battery | Time running on battery | seconds |
| Number of Transfers | Count of transfers to battery | - |

### Buttons
| Button | Description |
|--------|-------------|
| Refresh | Force refresh of UPS status |

## Automation Examples

### Notify on UPS power loss
```yaml
alias: UPS Power Loss Alert
trigger:
  - platform: state
    entity_id: sensor.my_ups_status
    from: "Online"
    to: "On Battery"
action:
  - service: notify.mobile_app_your_phone
    data:
      title: "⚠️ UPS Power Loss"
      message: "UPS is now running on battery power. {{ states('sensor.my_ups_time_left') }} minutes remaining."
```

### Notify when power is restored
```yaml
alias: UPS Power Restored
trigger:
  - platform: state
    entity_id: sensor.my_ups_status
    from: "On Battery"
    to: "Online"
action:
  - service: notify.mobile_app_your_phone
    data:
      title: "✅ Power Restored"
      message: "UPS is back on line power. Battery at {{ states('sensor.my_ups_battery_charge') }}%."
```

### Low battery warning
```yaml
alias: UPS Low Battery Warning
trigger:
  - platform: numeric_state
    entity_id: sensor.my_ups_battery_charge
    below: 30
condition:
  - condition: state
    entity_id: sensor.my_ups_status
    state: "On Battery"
action:
  - service: notify.mobile_app_your_phone
    data:
      title: "🔋 UPS Battery Low"
      message: "UPS battery is at {{ states('sensor.my_ups_battery_charge') }}%. Only {{ states('sensor.my_ups_time_left') }} minutes remaining!"
      data:
        priority: high
```

### Graceful shutdown on critical battery
```yaml
alias: UPS Critical Shutdown
trigger:
  - platform: numeric_state
    entity_id: sensor.my_ups_battery_charge
    below: 10
condition:
  - condition: state
    entity_id: sensor.my_ups_status
    state: "On Battery"
action:
  - service: hassio.host_shutdown
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
