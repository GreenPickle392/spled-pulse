"""Number platform for SPLED Pulse (effect speed, microphone sensitivity)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SPLedConfigEntry
from .entity import SPLedEntity
from .protocol import (
    SENSITIVITY_MAX,
    SENSITIVITY_MIN,
    SPEED_MAX,
    SPEED_MIN,
    DeviceState,
    cmd_sensitivity,
    cmd_speed,
)


@dataclass(frozen=True, kw_only=True)
class SPLedNumberDescription(NumberEntityDescription):
    """Describes a numeric controller setting."""

    field: str
    value_fn: Callable[[DeviceState], int]
    command_fn: Callable[[int], bytes]


NUMBERS: tuple[SPLedNumberDescription, ...] = (
    SPLedNumberDescription(
        key="effect_speed",
        translation_key="effect_speed",
        native_min_value=SPEED_MIN,
        native_max_value=SPEED_MAX,
        native_step=1,
        mode=NumberMode.SLIDER,
        field="speed",
        value_fn=lambda state: state.speed,
        command_fn=cmd_speed,
    ),
    SPLedNumberDescription(
        key="sensitivity",
        translation_key="sensitivity",
        native_min_value=SENSITIVITY_MIN,
        native_max_value=SENSITIVITY_MAX,
        native_step=1,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        field="sensitivity",
        value_fn=lambda state: state.sensitivity,
        command_fn=cmd_sensitivity,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SPLedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(SPLedNumber(entry, description) for description in NUMBERS)


class SPLedNumber(SPLedEntity, NumberEntity):
    """A numeric controller setting."""

    entity_description: SPLedNumberDescription

    def __init__(self, entry: SPLedConfigEntry, description: SPLedNumberDescription) -> None:
        super().__init__(entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int:
        return self.entity_description.value_fn(self.device_state)

    async def async_set_native_value(self, value: float) -> None:
        level = int(
            min(
                self.entity_description.native_max_value,
                max(self.entity_description.native_min_value, round(value)),
            )
        )
        await self._async_send(
            [self.entity_description.command_fn(level)],
            {self.entity_description.field: level},
        )
