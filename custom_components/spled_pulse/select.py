"""Select platform for SPLED Pulse (audio input, power-on behaviour)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SPLedConfigEntry
from .entity import SPLedEntity
from .protocol import AudioInput, PowerOnState, cmd_audio_input, cmd_power_on_state


@dataclass(frozen=True, kw_only=True)
class SPLedSelectDescription(SelectEntityDescription):
    """Describes an enumerated controller setting.

    Option keys are the lower-cased enum member names, translated in strings.json.
    """

    field: str
    enum: type[IntEnum]
    command_fn: Callable[[int], bytes]


SELECTS: tuple[SPLedSelectDescription, ...] = (
    SPLedSelectDescription(
        key="audio_input",
        translation_key="audio_input",
        options=[member.name.lower() for member in AudioInput],
        field="audio_input",
        enum=AudioInput,
        command_fn=cmd_audio_input,
    ),
    SPLedSelectDescription(
        key="power_on_state",
        translation_key="power_on_state",
        entity_category=EntityCategory.CONFIG,
        options=[member.name.lower() for member in PowerOnState],
        field="power_on_state",
        enum=PowerOnState,
        command_fn=cmd_power_on_state,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SPLedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(SPLedSelect(entry, description) for description in SELECTS)


class SPLedSelect(SPLedEntity, SelectEntity):
    """An enumerated controller setting."""

    entity_description: SPLedSelectDescription

    def __init__(self, entry: SPLedConfigEntry, description: SPLedSelectDescription) -> None:
        super().__init__(entry, description.key)
        self.entity_description = description

    @property
    def current_option(self) -> str | None:
        raw = getattr(self.device_state, self.entity_description.field)
        try:
            return self.entity_description.enum(raw).name.lower()
        except ValueError:
            return None

    async def async_select_option(self, option: str) -> None:
        value = self.entity_description.enum[option.upper()]
        await self._async_send(
            [self.entity_description.command_fn(value)],
            {self.entity_description.field: int(value)},
        )
