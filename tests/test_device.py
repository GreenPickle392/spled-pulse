"""Tests for the persistent connection manager, using a simulated controller."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from conftest import status_frame

from spled_pulse import device as dev
from spled_pulse import protocol as p


class FakeController:
    """Simulates the controller side of the link."""

    def __init__(self) -> None:
        self.responsive = True
        self.fail_connects = 0
        self.fail_next_write = False
        self.chunk_size = 20  # default ATT MTU payload, forces reassembly
        self.frame = status_frame()
        self.clients: list[FakeClient] = []
        self.written: list[bytes] = []

    async def establish_connection(
        self,
        _client_class: Any,
        _device: BLEDevice,
        _name: str,
        disconnected_callback: Callable[[Any], None],
        **_kwargs: Any,
    ) -> FakeClient:
        await asyncio.sleep(0)
        if self.fail_connects:
            self.fail_connects -= 1
            raise BleakError("simulated connect failure")
        client = FakeClient(self, disconnected_callback)
        self.clients.append(client)
        return client

    @property
    def client(self) -> FakeClient:
        return self.clients[-1]


class FakeServices:
    def get_characteristic(self, uuid: str) -> object | None:
        return object() if uuid == dev.CHAR_UUID else None


class FakeClient:
    def __init__(self, ctrl: FakeController, on_disconnect: Callable[[Any], None]) -> None:
        self._ctrl = ctrl
        self._on_disconnect = on_disconnect
        self._notify: Callable[[Any, bytearray], None] | None = None
        self.is_connected = True
        self.services = FakeServices()

    async def start_notify(self, _char: object, callback: Callable[..., None]) -> None:
        self._notify = callback

    async def clear_cache(self) -> None:
        pass

    async def write_gatt_char(self, _char: object, data: bytes, response: bool) -> None:
        assert response is True
        if not self.is_connected:
            raise BleakError("not connected")
        if self._ctrl.fail_next_write:
            self._ctrl.fail_next_write = False
            raise BleakError("simulated write failure")
        self._ctrl.written.append(bytes(data))
        if data == p.cmd_query_status() and self._ctrl.responsive:
            frame = self._ctrl.frame
            size = self._ctrl.chunk_size
            loop = asyncio.get_running_loop()
            for start in range(0, len(frame), size):
                loop.call_soon(self._notify, None, bytearray(frame[start : start + size]))

    async def disconnect(self) -> None:
        self.drop()

    def drop(self) -> None:
        """Simulate the radio link going away."""
        if self.is_connected:
            self.is_connected = False
            self._on_disconnect(self)


@pytest.fixture
def ctrl(monkeypatch: pytest.MonkeyPatch) -> FakeController:
    controller = FakeController()
    monkeypatch.setattr(dev, "establish_connection", controller.establish_connection)
    monkeypatch.setattr(dev, "RECONNECT_MIN_DELAY", 0.01)
    monkeypatch.setattr(dev, "RECONNECT_MAX_DELAY", 0.05)
    monkeypatch.setattr(dev, "STATUS_TIMEOUT", 0.2)
    monkeypatch.setattr(dev, "COMMAND_CONNECT_TIMEOUT", 2.0)
    monkeypatch.setattr(dev, "COMMAND_GAP", 0.0)
    monkeypatch.setattr(dev, "REFRESH_DELAY", 0.01)
    return controller


@pytest.fixture
async def device(ctrl: FakeController):
    ble = BLEDevice("AA:BB:CC:DD:EE:FF", "SP642E", None)
    d = dev.SPLedDevice(ble, name="Test", keepalive_interval=0.1)
    yield d
    await d.stop()


async def wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_connects_and_reads_fragmented_status(device: dev.SPLedDevice) -> None:
    updates: list[None] = []
    device.register_callback(lambda: updates.append(None))
    await device.start()
    assert await device.wait_ready(2)
    assert device.available
    assert device.state is not None and device.state.firmware == "V1.2.3"
    assert device.stats.connects == 1
    assert updates


async def test_reconnects_after_link_drop(device: dev.SPLedDevice, ctrl: FakeController) -> None:
    await device.start()
    assert await device.wait_ready(2)
    ctrl.client.drop()
    # Stays available during the grace period instead of flapping.
    assert device.available
    await wait_until(lambda: device.stats.connects == 2 and device.connected)
    assert device.stats.disconnects == 1


async def test_dead_link_detected_by_keepalive(
    device: dev.SPLedDevice, ctrl: FakeController
) -> None:
    await device.start()
    assert await device.wait_ready(2)
    ctrl.responsive = False  # link "up" but peer silent, like a BlueZ zombie
    await wait_until(lambda: device.stats.keepalive_failures >= 1)
    ctrl.responsive = True
    await wait_until(lambda: device.stats.connects >= 2 and device.connected)


async def test_retries_failed_connects_with_backoff(
    device: dev.SPLedDevice, ctrl: FakeController
) -> None:
    ctrl.fail_connects = 4
    await device.start()
    assert await device.wait_ready(3)
    assert device.stats.failed_attempts == 4
    assert device.stats.last_error == "BleakError: simulated connect failure"


async def test_unexpected_exception_does_not_kill_supervisor(
    device: dev.SPLedDevice, ctrl: FakeController, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = ctrl.establish_connection
    calls = 0

    async def flaky(*args: Any, **kwargs: Any) -> FakeClient:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AttributeError("boom")  # the class of bug that broke uniled
        return await original(*args, **kwargs)

    monkeypatch.setattr(dev, "establish_connection", flaky)
    await device.start()
    assert await device.wait_ready(3)


async def test_send_applies_optimistic_state_and_refreshes(
    device: dev.SPLedDevice, ctrl: FakeController
) -> None:
    await device.start()
    assert await device.wait_ready(2)
    ctrl.written.clear()
    await device.async_send([p.cmd_power(False)], {"power": False})
    assert ctrl.written[0] == p.cmd_power(False)
    assert device.state is not None and device.state.power is False
    # A follow-up status query confirms the real state.
    await wait_until(lambda: p.cmd_query_status() in ctrl.written)


async def test_send_recovers_from_failed_write(
    device: dev.SPLedDevice, ctrl: FakeController
) -> None:
    await device.start()
    assert await device.wait_ready(2)
    ctrl.fail_next_write = True
    await device.async_send([p.cmd_power(True)])
    assert device.stats.command_retries == 1
    assert ctrl.written.count(p.cmd_power(True)) == 1
    assert device.stats.connects == 2


async def test_send_while_disconnected_waits_for_reconnect(
    device: dev.SPLedDevice, ctrl: FakeController
) -> None:
    await device.start()
    assert await device.wait_ready(2)
    ctrl.client.drop()
    await device.async_send([p.cmd_power(True)])
    assert p.cmd_power(True) in ctrl.written


async def test_send_raises_when_unreachable(
    device: dev.SPLedDevice, ctrl: FakeController, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dev, "COMMAND_CONNECT_TIMEOUT", 0.2)
    await device.start()
    assert await device.wait_ready(2)
    ctrl.fail_connects = 10_000
    ctrl.client.drop()
    with pytest.raises(dev.DeviceUnavailableError):
        await device.async_send([p.cmd_power(True)])


async def test_becomes_unavailable_after_grace(
    device: dev.SPLedDevice, ctrl: FakeController, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dev, "UNAVAILABLE_GRACE", 0.1)
    await device.start()
    assert await device.wait_ready(2)
    ctrl.fail_connects = 10_000
    ctrl.client.drop()
    assert device.available
    await wait_until(lambda: not device.available)


async def test_wait_ready_times_out(device: dev.SPLedDevice, ctrl: FakeController) -> None:
    ctrl.fail_connects = 10_000
    await device.start()
    assert not await device.wait_ready(0.2)


async def test_stop_releases_connection(device: dev.SPLedDevice, ctrl: FakeController) -> None:
    await device.start()
    assert await device.wait_ready(2)
    await device.stop()
    assert not ctrl.client.is_connected
    assert not device.connected
    with pytest.raises(dev.DeviceUnavailableError):
        await device.async_send([p.cmd_power(True)])


async def test_advertisement_updates_device_and_wakes(
    device: dev.SPLedDevice, ctrl: FakeController, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dev, "RECONNECT_MAX_DELAY", 30.0)
    ctrl.fail_connects = 3  # pushes backoff well past the test timeout
    await device.start()
    await wait_until(lambda: device.stats.failed_attempts == 3)
    fresh = BLEDevice("AA:BB:CC:DD:EE:FF", "SP642E", {"path": "new"})
    await asyncio.sleep(0.05)
    device.update_ble_device(fresh)
    assert await device.wait_ready(1)


async def test_command_cuts_long_backoff_short(
    device: dev.SPLedDevice, ctrl: FakeController, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dev, "RECONNECT_MIN_DELAY", 0.1)
    monkeypatch.setattr(dev, "RECONNECT_MAX_DELAY", 30.0)
    monkeypatch.setattr(dev, "COMMAND_CONNECT_TIMEOUT", 1.0)
    ctrl.fail_connects = 4  # backoff now far longer than the command timeout
    await device.start()
    await wait_until(lambda: device.stats.failed_attempts == 4)
    # Arrives during the short minimum pause; must not be swallowed.
    await device.async_send([p.cmd_power(True)])
    assert p.cmd_power(True) in ctrl.written
