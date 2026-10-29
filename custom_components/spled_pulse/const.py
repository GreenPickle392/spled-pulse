"""Constants for SPLED Pulse."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "spled_pulse"
MANUFACTURER: Final = "SPLED (BanlanX)"

CONF_MODEL: Final = "model"
CONF_KEEPALIVE: Final = "keepalive_interval"
CONF_MIN_KELVIN: Final = "min_kelvin"
CONF_MAX_KELVIN: Final = "max_kelvin"

DEFAULT_KEEPALIVE: Final = 30
DEFAULT_MIN_KELVIN: Final = 2700
DEFAULT_MAX_KELVIN: Final = 6500

STARTUP_TIMEOUT: Final = 30.0
