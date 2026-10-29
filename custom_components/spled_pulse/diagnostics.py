"""Diagnostics for SPLED Pulse (Bluetooth addresses are redacted)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.bluetooth import async_last_service_info
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from . import SPLedConfigEntry
from .protocol import describe_advertisement

TO_REDACT = {CONF_ADDRESS, "address", "unique_id", "title"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SPLedConfigEntry
) -> dict[str, Any]:
    device = entry.runtime_data
    state = device.state
    info = async_last_service_info(hass, entry.data[CONF_ADDRESS], connectable=True)
    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            TO_REDACT,
        ),
        "connected": device.connected,
        "available": device.available,
        "connection_stats": asdict(device.stats),
        "state": asdict(state) if state else None,
        "advertisement": (
            {
                "rssi": info.rssi,
                "summary": describe_advertisement(
                    info.manufacturer_data, info.name, info.service_uuids
                ),
            }
            if info
            else None
        ),
    }
