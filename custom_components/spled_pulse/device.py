"""Persistent BLE connection manager for SP642E controllers.

A single supervisor task owns the connection. It connects, subscribes to
notifications, polls the controller as a keepalive and reconnects with
exponential backoff whenever the link drops or stops answering. Fresh
advertisements cut the backoff short, so the controller is picked up again
as soon as it is reachable.

No Home Assistant imports: the integration injects everything it needs.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    establish_connection,
)

from .protocol import (
    CMD_STATUS,
    DeviceState,
    FrameAssembler,
    ProtocolError,
    cmd_query_status,
    parse_status,
)

_LOGGER = logging.getLogger(__name__)

CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

CONNECT_ATTEMPTS = 3
STATUS_TIMEOUT = 6.0
WRITE_TIMEOUT = 5.0
DISCONNECT_TIMEOUT = 5.0
STOP_TIMEOUT = 5.0
COMMAND_CONNECT_TIMEOUT = 20.0
COMMAND_GAP = 0.15
REFRESH_DELAY = 0.6
RECONNECT_MIN_DELAY = 1.0
RECONNECT_MAX_DELAY = 60.0
# Keep entities available across short reconnects instead of flapping.
UNAVAILABLE_GRACE = 45.0

COMMUNICATION_ERRORS = (BleakError, TimeoutError, OSError, EOFError)


def _describe(err: BaseException) -> str:
    """Readable error text; TimeoutError and friends have an empty str()."""
    return f"{type(err).__name__}: {err}" if str(err) else type(err).__name__


class DeviceUnavailableError(Exception):
    """The controller could not be reached in time."""


class DeviceCommandError(Exception):
    """A command could not be delivered."""


class CharacteristicMissingError(BleakError):
    """The control characteristic was not found."""


@dataclass(slots=True)
class ConnectionStats:
    """Connection health counters, exposed through diagnostics."""

    connects: int = 0
    disconnects: int = 0
    failed_attempts: int = 0
    keepalive_failures: int = 0
    command_retries: int = 0
    resends: int = 0
    connected_since: float | None = None
    last_error: str | None = None


class SPLedDevice:
    """Keeps a single SP642E connected and mirrors its state."""

    def __init__(
        self,
        ble_device: BLEDevice,
        *,
        name: str,
        ble_device_callback: Callable[[], BLEDevice | None] | None = None,
        keepalive_interval: float = 30.0,
    ) -> None:
        self._ble_device = ble_device
        self._name = name
        self._ble_device_callback = ble_device_callback
        self._keepalive_interval = keepalive_interval

        self._client: BleakClientWithServiceCache | None = None
        self._char: BleakGATTCharacteristic | None = None
        self._assembler = FrameAssembler()
        self._state: DeviceState | None = None
        self._listeners: list[Callable[[], None]] = []

        self._connected = asyncio.Event()
        self._link_lost = asyncio.Event()
        self._status_received = asyncio.Event()
        self._wake = asyncio.Event()
        self._write_lock = asyncio.Lock()

        self._runner: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._refresh_handle: asyncio.TimerHandle | None = None
        self._grace_handle: asyncio.TimerHandle | None = None
        self._lost_at: float | None = None
        self._command_waiters = 0
        self._write_response = False
        self._pending: list[tuple[Sequence[bytes], dict[str, Any]]] = []
        self._stopping = False

        self.stats = ConnectionStats()

    # ------------------------------------------------------------------ public

    @property
    def name(self) -> str:
        return self._name

    @property
    def address(self) -> str:
        return self._ble_device.address

    @property
    def state(self) -> DeviceState | None:
        return self._state

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    @property
    def available(self) -> bool:
        """True while connected, and for a short grace period after a drop."""
        if self._state is None:
            return False
        if self._connected.is_set():
            return True
        return self._lost_at is not None and time.monotonic() - self._lost_at < UNAVAILABLE_GRACE

    def register_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Call ``callback`` on every state or availability change."""
        self._listeners.append(callback)

        def _remove() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(callback)

        return _remove

    def update_ble_device(self, ble_device: BLEDevice) -> None:
        """Take the latest BLEDevice from an advertisement.

        Always accepted (the adapter path may have changed); when the link is
        down this also cuts the reconnect backoff short.
        """
        self._ble_device = ble_device
        if not self._connected.is_set():
            self._wake.set()

    async def start(self) -> None:
        if self._runner is None:
            self._stopping = False
            self._runner = asyncio.create_task(self._run(), name=f"spled_pulse {self.address}")

    async def wait_ready(self, seconds: float) -> bool:
        """Wait until connected; a connection always comes with a fresh status."""
        try:
            await asyncio.wait_for(self._connected.wait(), seconds)
        except TimeoutError:
            return False
        return True

    async def stop(self) -> None:
        self._stopping = True
        self._cancel_handles()
        for task in (self._refresh_task, self._runner):
            if task and not task.done():
                task.cancel()
                # Bounded: a cancellation swallowed by a racing wait_for must
                # never block Home Assistant shutdown or an entry reload.
                await asyncio.wait({task}, timeout=STOP_TIMEOUT)
        self._runner = None
        self._refresh_task = None
        await self._teardown()

    async def async_send(
        self,
        packets: Sequence[bytes],
        optimistic: dict[str, Any] | None = None,
    ) -> None:
        """Deliver commands, reconnecting once if the link turns out dead.

        ``optimistic`` fields are applied to the local state right away so the
        UI responds instantly; a status query shortly after confirms them.
        """
        if not packets:
            return
        await self._ensure_connected()
        try:
            await self._write(packets)
        except COMMUNICATION_ERRORS as err:
            _LOGGER.debug("%s: write failed (%s), reconnecting", self._name, _describe(err))
            self.stats.command_retries += 1
            await self._drop_link()
            await self._ensure_connected()
            try:
                await self._write(packets)
            except COMMUNICATION_ERRORS as retry_err:
                raise DeviceCommandError(_describe(retry_err)) from retry_err

        if optimistic and self._state is not None:
            self._state = self._state.with_changes(**optimistic)
            self._pending.append((packets, optimistic))
            self._notify_listeners()
        self._schedule_refresh()

    # ------------------------------------------------------------- supervisor

    async def _run(self) -> None:
        backoff = RECONNECT_MIN_DELAY
        while not self._stopping:
            try:
                await self._connect()
                backoff = RECONNECT_MIN_DELAY
                await self._supervise()
            except asyncio.CancelledError:
                raise
            except COMMUNICATION_ERRORS as err:
                self.stats.failed_attempts += 1
                self.stats.last_error = _describe(err)
                _LOGGER.debug("%s: connection error: %s", self._name, _describe(err))
            except Exception as err:
                self.stats.failed_attempts += 1
                self.stats.last_error = _describe(err)
                _LOGGER.exception("%s: unexpected error", self._name)
            finally:
                await self._teardown()

            if self._stopping:
                break
            _LOGGER.debug("%s: reconnecting in up to %.0fs", self._name, backoff)
            await self._backoff(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_DELAY)

    async def _backoff(self, delay: float) -> None:
        # Always pause briefly so a burst of advertisements cannot hammer the
        # adapter, then let the next advertisement (or a command) wake us.
        await asyncio.sleep(RECONNECT_MIN_DELAY)
        if not self._command_waiters:
            self._wake.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), max(0.0, delay - RECONNECT_MIN_DELAY))

    async def _connect(self) -> None:
        _LOGGER.debug("%s: connecting to %s", self._name, self.address)
        self._link_lost.clear()
        self._assembler.reset()
        self._pending.clear()  # a fresh status supersedes unverified commands
        client = await establish_connection(
            BleakClientWithServiceCache,
            self._current_ble_device(),
            self._name,
            self._on_disconnect,
            max_attempts=CONNECT_ATTEMPTS,
            ble_device_callback=self._current_ble_device,
        )
        self._client = client

        char = client.services.get_characteristic(CHAR_UUID)
        if char is None:
            # Stale GATT cache is the usual cause; next attempt rediscovers.
            await client.clear_cache()
            raise CharacteristicMissingError(f"characteristic {CHAR_UUID} missing")
        self._char = char
        # Write-with-response stalls the whole ATT queue when the controller
        # skips an acknowledgement (seen on firmware V3.0.06), so prefer
        # unacknowledged writes and verify via status instead.
        self._write_response = "write-without-response" not in char.properties
        await client.start_notify(char, self._on_notify)
        # Only a link that answers a status query counts as connected.
        await self._request_status()

        self._lost_at = None
        self._cancel_grace()
        self.stats.connects += 1
        self.stats.connected_since = time.time()
        self._connected.set()
        _LOGGER.info("%s: connected", self._name)
        self._notify_listeners()

    async def _supervise(self) -> None:
        """Return when the link is lost; raise when the keepalive fails."""
        while not self._stopping:
            try:
                await asyncio.wait_for(self._link_lost.wait(), self._keepalive_interval)
            except TimeoutError:
                pass
            else:
                _LOGGER.info("%s: connection lost", self._name)
                return
            try:
                await self._request_status()
            except COMMUNICATION_ERRORS:
                # BlueZ can report a link as up long after the peer is gone.
                self.stats.keepalive_failures += 1
                _LOGGER.info("%s: keepalive failed, reconnecting", self._name)
                raise

    async def _teardown(self) -> None:
        self._mark_lost()
        client, self._client = self._client, None
        self._char = None
        if client is not None:
            with contextlib.suppress(*COMMUNICATION_ERRORS):
                await asyncio.wait_for(client.disconnect(), DISCONNECT_TIMEOUT)

    async def _drop_link(self) -> None:
        """Make the supervisor reconnect now (used when a write fails)."""
        self._mark_lost()
        self._link_lost.set()
        if (client := self._client) is not None:
            with contextlib.suppress(*COMMUNICATION_ERRORS):
                await asyncio.wait_for(client.disconnect(), DISCONNECT_TIMEOUT)

    # ---------------------------------------------------------------- helpers

    def _mark_lost(self) -> None:
        """Record the moment a working link went away (idempotent)."""
        if not self._connected.is_set():
            return
        self._connected.clear()
        self._lost_at = time.monotonic()
        self.stats.disconnects += 1
        self.stats.connected_since = None
        self._start_grace()
        self._notify_listeners()

    def _current_ble_device(self) -> BLEDevice:
        if self._ble_device_callback and (fresh := self._ble_device_callback()):
            self._ble_device = fresh
        return self._ble_device

    async def _ensure_connected(self) -> None:
        if self._connected.is_set():
            return
        if self._runner is None or self._runner.done():
            raise DeviceUnavailableError("connection manager is not running")
        self._command_waiters += 1
        self._wake.set()
        try:
            await asyncio.wait_for(self._connected.wait(), COMMAND_CONNECT_TIMEOUT)
        except TimeoutError as err:
            raise DeviceUnavailableError(
                f"{self._name} did not reconnect within {COMMAND_CONNECT_TIMEOUT:.0f}s"
            ) from err
        finally:
            self._command_waiters -= 1

    async def _write(self, packets: Sequence[bytes]) -> None:
        async with self._write_lock:
            client, char = self._client, self._char
            if client is None or char is None or not client.is_connected:
                raise BleakError("not connected")
            for index, packet in enumerate(packets):
                if index:
                    await asyncio.sleep(COMMAND_GAP)
                _LOGGER.debug("%s: -> %s", self._name, packet.hex())
                try:
                    await asyncio.wait_for(
                        client.write_gatt_char(char, packet, response=self._write_response),
                        WRITE_TIMEOUT,
                    )
                except COMMUNICATION_ERRORS:
                    raise
                except Exception as err:
                    # Bleak raises e.g. AssertionError when the link is torn down mid-call.
                    raise BleakError(_describe(err)) from err

    async def _request_status(self) -> None:
        self._status_received.clear()
        await self._write([cmd_query_status()])
        await asyncio.wait_for(self._status_received.wait(), STATUS_TIMEOUT)

    def _schedule_refresh(self) -> None:
        if self._refresh_handle is not None:
            self._refresh_handle.cancel()
        self._refresh_handle = asyncio.get_running_loop().call_later(
            REFRESH_DELAY, self._start_refresh
        )

    def _start_refresh(self) -> None:
        self._refresh_handle = None
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._refresh())

    async def _refresh(self) -> None:
        if not self._connected.is_set():
            return
        pending, self._pending = self._pending, []
        try:
            await self._request_status()
            # Commands sent while we waited for the status are newer: they win.
            if resend := self._unapplied(pending, superseded=self._pending):
                # Write commands are unacknowledged; resend once what didn't stick.
                _LOGGER.debug("%s: %d command(s) not applied, resending", self._name, len(resend))
                self.stats.resends += len(resend)
                for packets in resend:
                    await self._write(packets)
                await self._request_status()
        except COMMUNICATION_ERRORS as err:
            # The supervisor's keepalive deals with genuinely dead links.
            _LOGGER.debug("%s: refresh failed: %s", self._name, _describe(err))

    def _unapplied(
        self,
        pending: list[tuple[Sequence[bytes], dict[str, Any]]],
        superseded: list[tuple[Sequence[bytes], dict[str, Any]]],
    ) -> list[Sequence[bytes]]:
        """Commands whose expected fields don't match the reported state.

        Later commands win: a field is only checked against the newest
        command that set it, and never against one in ``superseded``.
        """
        if self._state is None:
            return []
        seen: set[str] = {key for _, expected in superseded for key in expected}
        resend: list[Sequence[bytes]] = []
        for packets, expected in reversed(pending):
            relevant = {key: value for key, value in expected.items() if key not in seen}
            seen.update(expected)
            if any(getattr(self._state, key) != value for key, value in relevant.items()):
                resend.append(packets)
        resend.reverse()
        return resend

    def _start_grace(self) -> None:
        self._cancel_grace()
        # Fire once the grace period ends so entities flip to unavailable.
        self._grace_handle = asyncio.get_running_loop().call_later(
            UNAVAILABLE_GRACE + 0.1, self._notify_listeners
        )

    def _cancel_grace(self) -> None:
        if self._grace_handle is not None:
            self._grace_handle.cancel()
            self._grace_handle = None

    def _cancel_handles(self) -> None:
        self._cancel_grace()
        if self._refresh_handle is not None:
            self._refresh_handle.cancel()
            self._refresh_handle = None

    def _notify_listeners(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("%s: listener failed", self._name)

    # -------------------------------------------------------------- callbacks

    def _on_disconnect(self, client: BleakClientWithServiceCache) -> None:
        if client is not self._client:
            return  # stale client from an earlier attempt
        self._mark_lost()
        self._link_lost.set()

    def _on_notify(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        for frame in self._assembler.feed(data):
            if frame[1] != CMD_STATUS:
                _LOGGER.debug("%s: <- ignored %s", self._name, frame.hex())
                continue
            try:
                state = parse_status(frame)
            except ProtocolError as err:
                _LOGGER.debug("%s: bad status frame (%s): %s", self._name, err, frame.hex())
                continue
            _LOGGER.debug("%s: <- %s", self._name, state)
            self._state = state
            self._status_received.set()
            self._notify_listeners()
