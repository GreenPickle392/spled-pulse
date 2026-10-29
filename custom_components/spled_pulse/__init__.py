"""SPLED Pulse: reliable local control of SPLED SP642E BLE LED controllers."""

from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_KEEPALIVE, DEFAULT_KEEPALIVE, DOMAIN, STARTUP_TIMEOUT
from .device import SPLedDevice

PLATFORMS: list[Platform] = [Platform.LIGHT, Platform.NUMBER, Platform.SELECT]

type SPLedConfigEntry = ConfigEntry[SPLedDevice]


async def async_setup_entry(hass: HomeAssistant, entry: SPLedConfigEntry) -> bool:
    """Set up an SP642E from a config entry."""
    address: str = entry.data[CONF_ADDRESS]

    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"address": address},
        )

    device = SPLedDevice(
        ble_device,
        name=entry.title,
        ble_device_callback=lambda: bluetooth.async_ble_device_from_address(
            hass, address, connectable=True
        ),
        keepalive_interval=entry.options.get(CONF_KEEPALIVE, DEFAULT_KEEPALIVE),
    )

    @callback
    def _async_on_advertisement(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        device.update_ble_device(service_info.device)

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _async_on_advertisement,
            bluetooth.BluetoothCallbackMatcher(address=address, connectable=True),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
    )

    await device.start()
    if not await device.wait_ready(STARTUP_TIMEOUT):
        await device.stop()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="device_no_response",
            translation_placeholders={"name": entry.title},
        )

    entry.runtime_data = device

    async def _async_stop(_event: Event) -> None:
        await device.stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop))
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SPLedConfigEntry) -> bool:
    """Unload a config entry and release the BLE connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.stop()
    return unloaded


async def _async_reload_on_options(hass: HomeAssistant, entry: SPLedConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
