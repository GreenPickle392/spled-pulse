"""Config flow for SPLED Pulse."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_KEEPALIVE,
    CONF_MAX_KELVIN,
    CONF_MIN_KELVIN,
    CONF_MODEL,
    DEFAULT_KEEPALIVE,
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    DOMAIN,
)
from .protocol import model_from_manufacturer_data


def _title(model: str, address: str) -> str:
    return f"{model} {address.replace(':', '')[-4:].upper()}"


class SPLedConfigFlow(ConfigFlow, domain=DOMAIN):
    """Discover and add SP642E controllers."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._model: str | None = None
        self._candidates: dict[str, tuple[str, BluetoothServiceInfoBleak]] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a controller found by Home Assistant's Bluetooth scanner."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        model = model_from_manufacturer_data(discovery_info.manufacturer_data)
        if model is None:
            return self.async_abort(reason="not_supported")
        self._discovery = discovery_info
        self._model = model
        self.context["title_placeholders"] = {"name": _title(model, discovery_info.address)}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovery is not None and self._model is not None
        if user_input is not None:
            return self._create_entry(self._discovery.address, self._model)
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": _title(self._model, self._discovery.address),
                "model": self._model,
            },
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Let the user pick from controllers seen nearby."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            if address not in self._candidates:
                return self.async_abort(reason="no_devices_found")
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self._create_entry(address, self._candidates[address][0])

        configured = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address in configured or info.address in self._candidates:
                continue
            if model := model_from_manufacturer_data(info.manufacturer_data):
                self._candidates[info.address] = (model, info)

        if not self._candidates:
            return self.async_abort(reason="no_devices_found")

        choices = {
            address: f"{_title(model, address)} ({info.name or address})"
            for address, (model, info) in self._candidates.items()
        }
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(choices)}),
        )

    @callback
    def _create_entry(self, address: str, model: str) -> ConfigFlowResult:
        return self.async_create_entry(
            title=_title(model, address),
            data={CONF_ADDRESS: address, CONF_MODEL: model},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SPLedOptionsFlow()


def _kelvin_selector() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=1500,
            max=10000,
            step=50,
            unit_of_measurement="K",
            mode=NumberSelectorMode.BOX,
        )
    )


class SPLedOptionsFlow(OptionsFlow):
    """Tune connection and colour temperature range."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_MIN_KELVIN] >= user_input[CONF_MAX_KELVIN]:
                errors["base"] = "invalid_kelvin_range"
            else:
                return self.async_create_entry(
                    data={key: int(value) for key, value in user_input.items()}
                )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_KEEPALIVE,
                    default=options.get(CONF_KEEPALIVE, DEFAULT_KEEPALIVE),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=10,
                        max=300,
                        step=5,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_MIN_KELVIN,
                    default=options.get(CONF_MIN_KELVIN, DEFAULT_MIN_KELVIN),
                ): _kelvin_selector(),
                vol.Required(
                    CONF_MAX_KELVIN,
                    default=options.get(CONF_MAX_KELVIN, DEFAULT_MAX_KELVIN),
                ): _kelvin_selector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
