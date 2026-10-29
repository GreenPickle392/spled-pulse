"""Diagnostics for SPLED Pulse (Bluetooth addresses are redacted)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from . import SPLedConfigEntry

TO_REDACT = {CONF_ADDRESS, "address", "unique_id", "title"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SPLedConfigEntry
) -> dict[str, Any]:
    device = entry.runtime_data
    state = device.state
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
    }
