"""Test setup.

The protocol and connection layers have no Home Assistant dependency, so they
are imported as a bare package without running the integration's __init__.
"""

import pathlib
import sys
import types

PKG_DIR = pathlib.Path(__file__).parents[1] / "custom_components" / "spled_pulse"
_pkg = types.ModuleType("spled_pulse")
_pkg.__path__ = [str(PKG_DIR)]
sys.modules.setdefault("spled_pulse", _pkg)


def status_frame(
    *,
    power: bool = True,
    mode: int = 0x02,
    effect: int = 0x01,
    brightness: int = 200,
    static_cw: tuple[int, int] = (100, 155),
    fx_cw: tuple[int, int] = (10, 20),
    speed: int = 5,
    loop: bool = False,
    gain: int = 8,
    audio_input: int = 0,
    power_on_state: int = 2,
    firmware: bytes = b"V1.2.3 ",
) -> bytes:
    payload_len = 50
    frame = bytearray(6 + payload_len)
    frame[0:6] = bytes((0x53, 0x02, 0x00, 0x01, 0x00, payload_len))
    frame[11:18] = firmware
    frame[19] = 0x03
    frame[25] = power_on_state
    frame[29] = 1 if power else 0
    frame[30] = 1 if loop else 0
    frame[32] = mode
    frame[33] = effect
    frame[36] = brightness
    frame[40], frame[41] = static_cw
    frame[42] = speed
    frame[45] = gain
    frame[46] = audio_input
    frame[50], frame[51] = fx_cw
    return bytes(frame)
