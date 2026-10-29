<p align="center">
  <img src="docs/icon.png" alt="SPLED Pulse" width="160">
</p>

<h1 align="center">SPLED Pulse</h1>

<p align="center">
  <b>Rock-solid local Bluetooth control of SPLED SP642E tunable-white LED controllers for Home Assistant.</b>
</p>

<p align="center">
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <a href="https://github.com/GreenPickle392/spled-pulse/actions/workflows/validate.yml"><img src="https://github.com/GreenPickle392/spled-pulse/actions/workflows/validate.yml/badge.svg" alt="Validate"></a>
  <a href="https://github.com/GreenPickle392/spled-pulse/releases"><img src="https://img.shields.io/github/v/release/GreenPickle392/spled-pulse" alt="Release"></a>
  <img src="https://img.shields.io/badge/iot__class-local__push-success" alt="local push">
</p>

---

SPLED Pulse keeps your **SP642E** (and the identical **SP632E**) connected and in sync with Home Assistant.
It was written for one job: an integration that **stays connected and gets the connection back on its own**
when the link drops.

## Why another integration?

The generic [UniLED](https://github.com/monty68/uniled) integration supports dozens of controllers, but with the
SP64xE family it often drops the connection and never recovers. SPLED Pulse is built only for this controller
and is designed around the connection:

| | UniLED | SPLED Pulse |
|---|---|---|
| Connection | Opened lazily by whichever poll or command comes first | **Persistent**, one owner task |
| After a drop | Waits for the next poll; retry path crashes ([`self._device` typo](https://github.com/monty68/uniled/blob/main/custom_components/uniled/lib/ble/device.py)) | **Reconnects right away** with backoff (1 s → 60 s) |
| Device comes back in range | Advertisements ignored while busy | **Next advertisement triggers reconnect** at once |
| "Zombie" links (BlueZ says connected, device silent) | Never detected | **Keepalive** detects them and reconnects |
| Short dropouts | Entities go *unavailable* | 45 s grace, no flapping in history and automations |
| Commands during a dropout | Fail | **Wait for the reconnect**, then send, with one retry |
| State changed by remote / app | Seen on next poll | **Pushed** by notifications, confirmed after every command |
| Fragmented BLE notifications | Dropped | Reassembled |

## Features

- 💡 **Light** with brightness, colour temperature (Kelvin) and effects: *Breath*, *Strobe*, *Heartbeat*, *Music*
- 🎚️ **Effect speed** and **microphone sensitivity**
- 🎤 **Audio input**: built-in mic, phone / player, or external mic
- 🔌 **Power-on behaviour**: stay off, turn on, or restore the last state
- 🔎 **Automatic discovery** through Home Assistant's Bluetooth integration
- 🩺 **Diagnostics** with connection statistics (reconnects, keepalive failures, last error); Bluetooth address redacted
- 🌍 English and German translations

## Requirements

- Home Assistant **2025.2** or newer
- A Bluetooth adapter that Home Assistant can use. The Raspberry Pi onboard adapter and [ESPHome Bluetooth proxies](https://esphome.io/components/bluetooth_proxy.html) both work.
- An SPLED **SP642E** or **SP632E** controller

> [!NOTE]
> The *2.4 GHz* in the controller's spec is for its RF remote. Home Assistant has no 2.4 GHz radio for that, so
> SPLED Pulse uses Bluetooth LE. The remote keeps working, and its changes show up in Home Assistant.

## Installation

### HACS (recommended)

1. In Home Assistant open **HACS → ⋮ → Custom repositories**.
2. Add `https://github.com/GreenPickle392/spled-pulse` with type **Integration**.
3. Search for **SPLED Pulse**, click **Download**, then **restart Home Assistant**.

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=GreenPickle392&repository=spled-pulse&category=integration)

### Manual

Copy `custom_components/spled_pulse` into your `config/custom_components/` folder and restart Home Assistant.

## Setup

> [!IMPORTANT]
> **Remove or disable UniLED for this controller first.** The controller accepts only one Bluetooth connection,
> and two integrations fighting over it is a common cause of dropouts. Also **close the phone app**: while the app
> is connected, Home Assistant cannot be.

Your controller is usually discovered automatically: you get a notification, then click **Add**.
Otherwise go to **Settings → Devices & services → Add integration → SPLED Pulse** and pick it from the list.

### Options

**Settings → Devices & services → SPLED Pulse → Configure**

| Option | Default | Meaning |
|---|---|---|
| Keepalive interval | 30 s | How often the link is checked. A dead link is replaced within this time. |
| Warmest colour temperature | 2700 K | Kelvin of your strip's warm-white LEDs |
| Coolest colour temperature | 6500 K | Kelvin of your strip's cool-white LEDs |

## Entities

| Entity | Type | Notes |
|---|---|---|
| *Controller name* | Light | On/off, brightness, colour temperature, effects. Set effect **off** for solid white. |
| Effect speed | Number (1–10) | Speed of the dynamic effects |
| Microphone sensitivity | Number (1–16) | Configuration |
| Audio input | Select | Source for the *Music* effect |
| Power-on behaviour | Select | Configuration |

In *Music* mode the brightness follows the sound, so brightness changes are ignored.

## Troubleshooting

- **Raspberry Pi onboard Bluetooth.** The Pi's Bluetooth and Wi-Fi share one chip and antenna. If dropouts persist, use
  Ethernet instead of Wi-Fi, keep the Pi away from metal enclosures, or add an inexpensive
  [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html) near the controller.
- **Check connection health:** device page → **⋮ → Download diagnostics** shows connects, disconnects, keepalive
  failures and the last error.
- **Debug logging:**
  ```yaml
  logger:
    logs:
      custom_components.spled_pulse: debug
      bleak_retry_connector: debug
  ```

## Development

```bash
pip install -r requirements_test.txt
ruff check . && pytest
```

The protocol (`protocol.py`) and the connection manager (`device.py`) have no Home Assistant imports. They are tested
against a simulated controller covering link drops, silent dead links, failed connects, failed writes and fragmented
notifications.

## Credits

Protocol details were worked out by [monty68/uniled](https://github.com/monty68/uniled) (MIT). Thanks!
This project is not affiliated with SPLED or BanlanX.

## License

[MIT](LICENSE)
