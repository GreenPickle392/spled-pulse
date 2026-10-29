"""Base entity for SPLED Pulse."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import Entity

from . import SPLedConfigEntry
from .const import CONF_MODEL, DOMAIN, MANUFACTURER
from .device import DeviceCommandError, DeviceUnavailableError, SPLedDevice
from .protocol import DeviceState


class SPLedEntity(Entity):
    """Push-updated entity bound to one controller."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: SPLedConfigEntry, key: str | None = None) -> None:
        self._device: SPLedDevice = entry.runtime_data
        address: str = entry.data[CONF_ADDRESS]
        self._attr_unique_id = f"{address}_{key}" if key else address
        state = self._device.state
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, address)},
            identifiers={(DOMAIN, address)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=entry.data.get(CONF_MODEL),
            sw_version=state.firmware if state else None,
        )

    @property
    def available(self) -> bool:
        return self._device.available

    @property
    def device_state(self) -> DeviceState:
        """Current state; only valid while the entity is available."""
        assert self._device.state is not None
        return self._device.state

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._device.register_callback(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    async def _async_send(
        self, packets: Sequence[bytes], optimistic: dict[str, Any] | None = None
    ) -> None:
        """Send commands, translating failures into user-facing errors."""
        try:
            await self._device.async_send(packets, optimistic)
        except DeviceUnavailableError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unavailable",
                translation_placeholders={"name": self._device.name},
            ) from err
        except DeviceCommandError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"name": self._device.name, "error": str(err)},
            ) from err
