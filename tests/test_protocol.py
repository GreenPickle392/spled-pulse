"""Tests for the wire protocol."""

import pytest
from conftest import status_frame

from spled_pulse import protocol as p


def test_encode_frame_layout() -> None:
    assert p.encode(0x50, b"\x01") == bytes.fromhex("53500001000101")
    assert p.cmd_query_status() == bytes.fromhex("53020001000101")


def test_encode_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        p.encode(0x100)
    with pytest.raises(ValueError):
        p.encode(0x50, bytes(256))


@pytest.mark.parametrize(
    ("packet", "expected"),
    [
        (p.cmd_power(True), "53500001000101"),
        (p.cmd_power(False), "53500001000100"),
        (p.cmd_brightness(128), "5351000100020180"),
        (p.cmd_brightness(0), "5351000100020101"),  # never send 0 = off
        (p.cmd_brightness(999), "53510001000201ff"),
        (p.cmd_cct(10, 245, p.Mode.STATIC_WHITE), "536100010002" + "0af5"),
        (p.cmd_cct(10, 245, p.Mode.DYNAMIC_WHITE), "536000010002" + "0af5"),
        (p.cmd_mode(p.Mode.DYNAMIC_WHITE, 2), "53530001000204" + "02"),
        (p.cmd_speed(0), "53540001000101"),
        (p.cmd_speed(50), "5354000100010a"),
        (p.cmd_sensitivity(99), "535a0001000110"),
        (p.cmd_audio_input(p.AudioInput.EXTERNAL_MIC), "53590001000102"),
        (p.cmd_power_on_state(p.PowerOnState.LAST), "530b0001000102"),
    ],
)
def test_commands(packet: bytes, expected: str) -> None:
    assert packet.hex() == expected


def test_parse_static_status() -> None:
    state = p.parse_status(status_frame())
    assert state.power is True
    assert state.mode == p.Mode.STATIC_WHITE
    assert (state.cold, state.warm) == (100, 155)
    assert state.brightness == 200
    assert state.firmware == "V1.2.3"
    assert state.power_on_state == p.PowerOnState.LAST
    assert state.effect_name is None


def test_parse_dynamic_status_uses_effect_cct() -> None:
    state = p.parse_status(status_frame(mode=0x04, effect=0x03))
    assert (state.cold, state.warm) == (10, 20)
    assert state.effect_name == "Heartbeat"


def test_parse_unknown_mode_has_no_effect_name() -> None:
    assert p.parse_status(status_frame(mode=0x7F)).effect_name is None


@pytest.mark.parametrize(
    "frame",
    [
        b"",
        status_frame()[:40],
        b"\x00" + status_frame()[1:],
        status_frame()[:1] + b"\x03" + status_frame()[2:],
        status_frame()[:2] + b"\x01" + status_frame()[3:],
    ],
)
def test_parse_rejects_bad_frames(frame: bytes) -> None:
    with pytest.raises(p.ProtocolError):
        p.parse_status(frame)


def test_assembler_handles_fragments_and_garbage() -> None:
    frame = status_frame()
    asm = p.FrameAssembler()
    chunks = [b"\xff\xee" + frame[:20], frame[20:40], frame[40:]]
    out: list[bytes] = []
    for chunk in chunks:
        out.extend(asm.feed(chunk))
    assert out == [frame]


def test_assembler_splits_back_to_back_frames() -> None:
    asm = p.FrameAssembler()
    ack = p.encode(0x50, b"\x01")
    assert asm.feed(ack + status_frame() + ack[:3]) == [ack, status_frame()]
    assert asm.feed(ack[3:]) == [ack]


def test_assembler_drops_pure_garbage() -> None:
    asm = p.FrameAssembler()
    assert asm.feed(b"\x00\x01\x02") == []
    assert asm.feed(p.cmd_power(True)) == [p.cmd_power(True)]


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({20563: bytes((0x2D, 0x10, 0x00))}, "SP642E"),
        ({20563: bytes((0x21, 0x10))}, "SP632E"),
        ({20563: bytes((0x2D, 0x11))}, None),
        ({20563: bytes((0x2E, 0x10))}, None),
        ({20563: b"\x2d"}, None),
        ({1234: bytes((0x2D, 0x10))}, None),
        ({}, None),
    ],
)
def test_model_detection(data: dict[int, bytes], expected: str | None) -> None:
    assert p.model_from_manufacturer_data(data) == expected


@pytest.mark.parametrize("kelvin", [2700, 3000, 4000, 5000, 6500])
def test_kelvin_round_trip(kelvin: int) -> None:
    cold, warm = p.kelvin_to_cold_warm(kelvin, 2700, 6500)
    assert cold + warm == 255
    assert abs(p.cold_warm_to_kelvin(cold, warm, 2700, 6500) - kelvin) <= 15


def test_kelvin_extremes_and_clamping() -> None:
    assert p.kelvin_to_cold_warm(1000, 2700, 6500) == (0, 255)
    assert p.kelvin_to_cold_warm(9000, 2700, 6500) == (255, 0)
    assert p.cold_warm_to_kelvin(0, 0, 2700, 6500) == 4600
    # Scale independent: a dimmed reading maps to the same temperature
    assert p.cold_warm_to_kelvin(50, 50, 2700, 6500) == p.cold_warm_to_kelvin(127, 127, 2700, 6500)


@pytest.mark.parametrize(
    ("data", "name", "uuids", "expected"),
    [
        ({20563: b"\x99\x99"}, None, [], True),  # unknown model byte, right maker
        ({}, "SP642E", [], True),
        ({}, "sp6xx", [], True),
        ({}, None, ["0000FFE0-0000-1000-8000-00805F9B34FB"], True),
        ({}, None, ["0000e0ff-0000-1000-8000-00805f9b34fb"], True),
        ({76: b"\x02\x15"}, "iPhone", ["0000180f-0000-1000-8000-00805f9b34fb"], False),
        ({}, None, [], False),
    ],
)
def test_looks_like_spled(
    data: dict[int, bytes], name: str | None, uuids: list[str], expected: bool
) -> None:
    assert p.looks_like_spled(data, name, uuids) is expected


def test_describe_advertisement() -> None:
    text = p.describe_advertisement({20563: b"\x2d\x10"}, "SP642E", ["x"])
    assert text == "name='SP642E' manufacturer_data={20563: 2d 10} service_uuids=['x']"
