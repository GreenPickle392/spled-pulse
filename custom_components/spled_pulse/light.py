"""Light platform for SPLED Pulse."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    EFFECT_OFF,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SPLedConfigEntry
from .const import (
    CONF_MAX_KELVIN,
    CONF_MIN_KELVIN,
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    DOMAIN,
)
from .entity import SPLedEntity
from .protocol import (
    EFFECT_SOLID,
    EFFECTS_BY_NAME,
    Mode,
    cmd_brightness,
    cmd_cct,
    cmd_mode,
    cmd_power,
    cold_warm_to_kelvin,
    kelvin_to_cold_warm,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SPLedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([SPLedLight(entry)])


class SPLedLight(SPLedEntity, LightEntity):
    """Tunable white LED strip."""

    _attr_name = None
    _attr_color_mode = ColorMode.COLOR_TEMP
    _attr_supported_color_modes = {ColorMode.COLOR_TEMP}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = [EFFECT_OFF, *EFFECTS_BY_NAME]

    def __init__(self, entry: SPLedConfigEntry) -> None:
        super().__init__(entry)
        self._attr_min_color_temp_kelvin = int(
            entry.options.get(CONF_MIN_KELVIN, DEFAULT_MIN_KELVIN)
        )
        self._attr_max_color_temp_kelvin = int(
            entry.options.get(CONF_MAX_KELVIN, DEFAULT_MAX_KELVIN)
        )

    @property
    def is_on(self) -> bool:
        return self.device_state.power

    @property
    def brightness(self) -> int | None:
        state = self.device_state
        # Sound modes drive the level from the audio, the controller reports 255.
        return 255 if state.is_sound_mode else max(1, state.brightness)

    @property
    def color_temp_kelvin(self) -> int:
        state = self.device_state
        return cold_warm_to_kelvin(
            state.cold,
            state.warm,
            self._attr_min_color_temp_kelvin,
            self._attr_max_color_temp_kelvin,
        )

    @property
    def effect(self) -> str:
        return self.device_state.effect_name or EFFECT_OFF

    async def async_turn_on(self, **kwargs: Any) -> None:
        state = self.device_state
        packets: list[bytes] = []
        changes: dict[str, Any] = {}
        mode = state.mode

        if (effect := kwargs.get(ATTR_EFFECT)) is not None:
            if effect == EFFECT_OFF:
                mode, effect_id = Mode.STATIC_WHITE, EFFECT_SOLID
            elif effect in EFFECTS_BY_NAME:
                mode, effect_id = EFFECTS_BY_NAME[effect]
            else:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_effect",
                    translation_placeholders={"effect": str(effect)},
                )
            packets.append(cmd_mode(mode, effect_id))
            changes.update(mode=int(mode), effect=effect_id)

        if (kelvin := kwargs.get(ATTR_COLOR_TEMP_KELVIN)) is not None:
            cold, warm = kelvin_to_cold_warm(
                kelvin,
                self._attr_min_color_temp_kelvin,
                self._attr_max_color_temp_kelvin,
            )
            packets.append(cmd_cct(cold, warm, mode))
            changes.update(cold=cold, warm=warm)

        if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None and mode != Mode.SOUND_WHITE:
            packets.append(cmd_brightness(brightness))
            changes.update(brightness=max(1, brightness))

        if not state.power or not packets:
            packets.insert(0, cmd_power(True))
            changes.update(power=True)

        await self._async_send(packets, changes)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_send([cmd_power(False)], {"power": False})
