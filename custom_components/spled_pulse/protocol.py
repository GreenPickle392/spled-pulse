"""Wire protocol for SPLED (BanlanX) SP642E / SP632E PWM CCT controllers.

Pure Python with no Home Assistant imports so it can be unit tested on its own.

Frame layout (both directions)::

    0x53 | cmd | key | 0x01 | 0x00 | len | payload[len]

``key`` is always 0x00 for these models (unencrypted).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Final

HEADER_BYTE: Final = 0x53
HEADER_LENGTH: Final = 6
MAX_FRAME_LENGTH: Final = HEADER_LENGTH + 0xFF

MANUFACTURER_ID: Final = 20563  # 0x5053, "SP"
# First manufacturer data byte identifies the model, second is always 0x10.
# 0x4A is reported by SP642E firmware V3.0.06.
MODEL_IDS: Final[dict[int, str]] = {0x2D: "SP642E", 0x4A: "SP642E", 0x21: "SP632E"}
MODEL_MARKER: Final = 0x10

CMD_STATUS: Final = 0x02
CMD_POWER_ON_STATE: Final = 0x0B
CMD_POWER: Final = 0x50
CMD_BRIGHTNESS: Final = 0x51
CMD_MODE: Final = 0x53
CMD_SPEED: Final = 0x54
CMD_LOOP: Final = 0x58
CMD_AUDIO_INPUT: Final = 0x59
CMD_SENSITIVITY: Final = 0x5A
CMD_CCT_DYNAMIC: Final = 0x60
CMD_CCT_STATIC: Final = 0x61

BRIGHTNESS_TARGET_WHITE: Final = 0x01

SPEED_MIN: Final = 1
SPEED_MAX: Final = 10
SENSITIVITY_MIN: Final = 1
SENSITIVITY_MAX: Final = 16


class Mode(IntEnum):
    """Light modes supported by the white-only PWM firmware."""

    STATIC_WHITE = 0x02
    DYNAMIC_WHITE = 0x04
    SOUND_WHITE = 0x06


class AudioInput(IntEnum):
    """Audio source for sound-reactive modes."""

    INTERNAL_MIC = 0x00
    PLAYER = 0x01
    EXTERNAL_MIC = 0x02


class PowerOnState(IntEnum):
    """What the controller does after mains power returns."""

    OFF = 0x00
    ON = 0x01
    LAST = 0x02


EFFECT_SOLID: Final = 0x01

# (mode, effect id) -> effect name shown in Home Assistant
EFFECTS: Final[dict[tuple[Mode, int], str]] = {
    (Mode.DYNAMIC_WHITE, 0x01): "Breath",
    (Mode.DYNAMIC_WHITE, 0x02): "Strobe",
    (Mode.DYNAMIC_WHITE, 0x03): "Heartbeat",
    (Mode.SOUND_WHITE, 0x01): "Music",
}
EFFECTS_BY_NAME: Final[dict[str, tuple[Mode, int]]] = {name: key for key, name in EFFECTS.items()}


class ProtocolError(ValueError):
    """Raised when a frame cannot be decoded."""


@dataclass(frozen=True, slots=True)
class DeviceState:
    """Snapshot of the controller state as reported by a status frame."""

    power: bool
    mode: int
    effect: int
    brightness: int
    cold: int
    warm: int
    speed: int
    loop: bool
    sensitivity: int
    audio_input: int
    power_on_state: int
    firmware: str

    @property
    def effect_name(self) -> str | None:
        """Name of the running effect, None for solid white."""
        try:
            return EFFECTS.get((Mode(self.mode), self.effect))
        except ValueError:
            return None

    @property
    def is_sound_mode(self) -> bool:
        return self.mode == Mode.SOUND_WHITE

    @property
    def is_static_mode(self) -> bool:
        return self.mode == Mode.STATIC_WHITE

    def with_changes(self, **changes: object) -> DeviceState:
        """Return a copy with the given fields replaced (optimistic updates)."""
        return replace(self, **changes)


def encode(cmd: int, payload: bytes | bytearray = b"") -> bytes:
    """Build a command frame."""
    if not 0 <= cmd <= 0xFF:
        raise ValueError(f"command out of range: {cmd}")
    if len(payload) > 0xFF:
        raise ValueError("payload too long")
    return bytes((HEADER_BYTE, cmd, 0x00, 0x01, 0x00, len(payload))) + bytes(payload)


def _byte(value: int, low: int = 0, high: int = 0xFF) -> int:
    """Clamp an int into a single protocol byte range."""
    return max(low, min(high, int(value)))


def cmd_query_status() -> bytes:
    return encode(CMD_STATUS, b"\x01")


def cmd_power(on: bool) -> bytes:
    return encode(CMD_POWER, bytes((0x01 if on else 0x00,)))


def cmd_brightness(level: int) -> bytes:
    return encode(CMD_BRIGHTNESS, bytes((BRIGHTNESS_TARGET_WHITE, _byte(level, 1))))


def cmd_cct(cold: int, warm: int, mode: int) -> bytes:
    cmd = CMD_CCT_STATIC if mode == Mode.STATIC_WHITE else CMD_CCT_DYNAMIC
    return encode(cmd, bytes((_byte(cold), _byte(warm))))


def cmd_mode(mode: Mode, effect: int) -> bytes:
    return encode(CMD_MODE, bytes((int(mode), _byte(effect))))


def cmd_speed(speed: int) -> bytes:
    return encode(CMD_SPEED, bytes((_byte(speed, SPEED_MIN, SPEED_MAX),)))


def cmd_loop(enabled: bool) -> bytes:
    return encode(CMD_LOOP, bytes((0x01 if enabled else 0x00,)))


def cmd_audio_input(source: AudioInput) -> bytes:
    return encode(CMD_AUDIO_INPUT, bytes((int(AudioInput(source)),)))


def cmd_sensitivity(gain: int) -> bytes:
    return encode(CMD_SENSITIVITY, bytes((_byte(gain, SENSITIVITY_MIN, SENSITIVITY_MAX),)))


def cmd_power_on_state(state: PowerOnState) -> bytes:
    return encode(CMD_POWER_ON_STATE, bytes((int(PowerOnState(state)),)))


# Offsets into a full status frame (header included).
_STATUS_MIN_LENGTH: Final = 53
_OFF_FIRMWARE: Final = slice(11, 18)
_OFF_POWER_ON_STATE: Final = 25
_OFF_POWER: Final = 29
_OFF_LOOP: Final = 30
_OFF_MODE: Final = 32
_OFF_EFFECT: Final = 33
_OFF_WHITE_LEVEL: Final = 36
_OFF_STATIC_COLD: Final = 40
_OFF_STATIC_WARM: Final = 41
_OFF_SPEED: Final = 42
_OFF_GAIN: Final = 45
_OFF_INPUT: Final = 46
_OFF_FX_COLD: Final = 50
_OFF_FX_WARM: Final = 51


def parse_status(frame: bytes | bytearray) -> DeviceState:
    """Decode a complete status frame."""
    if len(frame) < _STATUS_MIN_LENGTH:
        raise ProtocolError(f"status frame too short ({len(frame)} bytes)")
    if frame[0] != HEADER_BYTE or frame[1] != CMD_STATUS:
        raise ProtocolError("not a status frame")
    if frame[2] != 0x00:
        raise ProtocolError("encrypted frames are not supported")

    mode = frame[_OFF_MODE]
    if mode == Mode.STATIC_WHITE:
        cold, warm = frame[_OFF_STATIC_COLD], frame[_OFF_STATIC_WARM]
    else:
        cold, warm = frame[_OFF_FX_COLD], frame[_OFF_FX_WARM]

    return DeviceState(
        power=frame[_OFF_POWER] != 0,
        mode=mode,
        effect=frame[_OFF_EFFECT],
        brightness=frame[_OFF_WHITE_LEVEL],
        cold=cold,
        warm=warm,
        speed=frame[_OFF_SPEED],
        loop=bool(frame[_OFF_LOOP]),
        sensitivity=frame[_OFF_GAIN],
        audio_input=frame[_OFF_INPUT],
        power_on_state=frame[_OFF_POWER_ON_STATE],
        firmware=bytes(frame[_OFF_FIRMWARE]).decode("ascii", "replace").strip("\x00 "),
    )


class FrameAssembler:
    """Reassemble frames that arrive split across several notifications.

    With a small ATT MTU the 50+ byte status frame is delivered in chunks.
    Garbage before a header byte is discarded so one corrupt packet cannot
    wedge the stream.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, data: bytes | bytearray) -> list[bytes]:
        """Add received bytes, return every complete frame."""
        self._buffer.extend(data)
        frames: list[bytes] = []
        while self._buffer:
            if self._buffer[0] != HEADER_BYTE:
                start = self._buffer.find(HEADER_BYTE)
                if start < 0:
                    self._buffer.clear()
                    break
                del self._buffer[:start]
            if len(self._buffer) < HEADER_LENGTH:
                break
            total = HEADER_LENGTH + self._buffer[5]
            if len(self._buffer) < total:
                break
            frames.append(bytes(self._buffer[:total]))
            del self._buffer[:total]
        return frames


def model_from_manufacturer_data(data: dict[int, bytes]) -> str | None:
    """Return the model name if the advertisement belongs to a supported model."""
    payload = data.get(MANUFACTURER_ID)
    if payload is None or len(payload) < 2 or payload[1] != MODEL_MARKER:
        return None
    return MODEL_IDS.get(payload[0])


SERVICE_UUIDS: Final = frozenset(
    {"0000ffe0-0000-1000-8000-00805f9b34fb", "0000e0ff-0000-1000-8000-00805f9b34fb"}
)


def looks_like_spled(
    manufacturer_data: dict[int, bytes], name: str | None, service_uuids: list[str]
) -> bool:
    """Loose match for the manual setup list.

    Firmware revisions advertise differently, so this errs on the side of
    listing a device; setup then confirms it by asking for a status frame.
    """
    if MANUFACTURER_ID in manufacturer_data:
        return True
    if name and name.upper().startswith("SP6"):
        return True
    return any(uuid.lower() in SERVICE_UUIDS for uuid in service_uuids)


def describe_advertisement(
    manufacturer_data: dict[int, bytes], name: str | None, service_uuids: list[str]
) -> str:
    """One-line summary of an advertisement for logs and diagnostics."""
    maker = ", ".join(f"{mid}: {data.hex(' ')}" for mid, data in manufacturer_data.items())
    return f"name={name!r} manufacturer_data={{{maker}}} service_uuids={service_uuids}"


def kelvin_to_cold_warm(kelvin: int, min_kelvin: int, max_kelvin: int) -> tuple[int, int]:
    """Split a colour temperature into cold/warm channel ratios (sum = 255).

    Brightness is sent separately, so the channels only describe the mix.
    """
    kelvin = max(min_kelvin, min(max_kelvin, kelvin))
    cold_ratio = (kelvin - min_kelvin) / (max_kelvin - min_kelvin)
    cold = round(255 * cold_ratio)
    return cold, 255 - cold


def cold_warm_to_kelvin(cold: int, warm: int, min_kelvin: int, max_kelvin: int) -> int:
    """Inverse of :func:`kelvin_to_cold_warm`; tolerant of any channel scale."""
    total = cold + warm
    if total <= 0:
        return round((min_kelvin + max_kelvin) / 2)
    return round(min_kelvin + (max_kelvin - min_kelvin) * cold / total)
