"""Protocol constants and per-model memory addresses.

Reads use first frame byte 0x00. Writes (first byte 0x01) are the panel's
commands: an unexpected write can lock the panel until it is powered off, so
the library only builds the few writes listed in COMMANDS, each with its fixed
payload length.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

DEFAULT_PORT: Final[int] = 5004
DEFAULT_TIMEOUT: Final[float] = 5.0
# Pause after the optional TCP password, before the first request.
PASSWORD_DELAY: Final[float] = 0.4

READ: Final[int] = 0x00
WRITE: Final[int] = 0x01
# Largest payload asked for in a single read.
MAX_READ: Final[int] = 250

# Banks (second frame byte).
BANK_MAIN: Final[int] = 0x00   # live status and configuration
BANK_NAMES: Final[int] = 0x01  # 16-byte names

NAME_LENGTH: Final[int] = 16

# Live status, the same on every model.
FIRMWARE_ADDRESS: Final[int] = 0x004000
FIRMWARE_LENGTH: Final[int] = 12
# Panel serial number: 16 ASCII digits (read on a 1050 firmware 6.08).
SERIAL_ADDRESS: Final[int] = 0x00FEA0
SERIAL_LENGTH: Final[int] = 16
AREAS_STATUS_ADDRESS: Final[int] = 0x002000
ZONES_STATUS_ADDRESS: Final[int] = 0x002001
ZONES_ENABLED_ADDRESS: Final[int] = 0x002002
ZONES_ALARM_MEMORY_ADDRESS: Final[int] = 0x002003
AREAS_STATUS_LENGTH: Final[int] = 16
ZONES_STATUS_LENGTH: Final[int] = 26
ZONES_ENABLED_LENGTH: Final[int] = 26
ZONES_ALARM_MEMORY_LENGTH: Final[int] = 28

COMMAND_RESULT_ADDRESS: Final[int] = 0x002004
COMMAND_RESULT_LENGTH: Final[int] = 1
COMMAND_RESULT_OK: Final[int] = 0x00
COMMAND_RESULT_BAD_PIN: Final[int] = 0x01

# Commands: address -> payload length. Every payload starts with the 6-byte PIN.
ARM_ADDRESS: Final[int] = 0x002006         # PIN + 8 bytes, one nibble per area
RESET_MEMORY_ADDRESS: Final[int] = 0x002008  # PIN + 2 bytes area mask (LE)
EXCLUDE_ADDRESS: Final[int] = 0x002009     # PIN + 2 bytes physical index (LE) + 1/0
SCENARIO_ADDRESS: Final[int] = 0x00200A    # PIN + scenario number (0-based)
PIN_LENGTH: Final[int] = 6
COMMANDS: Final[dict[int, int]] = {
    ARM_ADDRESS: PIN_LENGTH + 8,
    RESET_MEMORY_ADDRESS: PIN_LENGTH + 2,
    EXCLUDE_ADDRESS: PIN_LENGTH + 3,
    SCENARIO_ADDRESS: PIN_LENGTH + 1,
}

# Configuration memory base: the per-model offsets below are relative to it.
CONFIG_BASE: Final[int] = 0x010000

# Terminal record: 12 bytes per logical terminal, byte 11 = terminal type.
TERMINAL_RECORD_SIZE: Final[int] = 12
TERMINAL_RECORD_TYPE_OFFSET: Final[int] = 11
TERMINAL_TYPE_ZONE: Final[int] = 0
TERMINAL_OUTPUT_TYPES: Final[tuple[int, ...]] = (1, 2)
# Zone record: 9 bytes per logical terminal, bytes 0-1 = area mask.
ZONE_RECORD_SIZE: Final[int] = 9

FREE: Final[int] = 0xFF


@dataclass(frozen=True)
class Model:
    """Sizes and addresses of one SmartLiving model."""

    name: str
    areas: int
    zones: int              # logical terminals
    scenarios: int
    physical_terminals: int
    # bank 0x01 (names)
    area_names: int
    zone_names: int
    scenario_names: int
    # configuration memory, relative to CONFIG_BASE
    terminal_table: int     # physical index -> logical terminal (zone - 1)
    terminal_records: int   # 12 bytes per logical terminal
    zone_records: int | None  # 9 bytes per logical terminal (area mask)
    verified: bool          # read on a real panel of this model


# 1050: read on a SmartLiving 1050 firmware 6.08.
# 10100: addresses from a SmartLiving 10100 firmware 6.09 dump (other project).
# 515/505: names like the 1050, terminal table and records from the installer
# memory map; not read on a real panel yet.
# zone_records (area of each zone) is known only for the 1050.
MODELS: Final[dict[str, Model]] = {
    "1050": Model(
        name="SmartLiving 1050", areas=10, zones=50, scenarios=30, physical_terminals=130,
        area_names=0x010000, zone_names=0x0100A0, scenario_names=0x011450,
        terminal_table=0x78FD, terminal_records=0x57D4, zone_records=0x5450,
        verified=True,
    ),
    "10100": Model(
        name="SmartLiving 10100", areas=15, zones=100, scenarios=30, physical_terminals=240,
        area_names=0x010000, zone_names=0x0100F0, scenario_names=0x012350,
        terminal_table=0xCF75, terminal_records=0x90CD, zone_records=None,
        verified=False,
    ),
    "515": Model(
        name="SmartLiving 515", areas=10, zones=20, scenarios=30, physical_terminals=70,
        area_names=0x010000, zone_names=0x0100A0, scenario_names=0x011450,
        terminal_table=0x4595, terminal_records=0x306F, zone_records=None,
        verified=False,
    ),
    "505": Model(
        name="SmartLiving 505", areas=10, zones=10, scenarios=30, physical_terminals=40,
        area_names=0x010000, zone_names=0x0100A0, scenario_names=0x011450,
        terminal_table=0x3DE0, terminal_records=0x2971, zone_records=None,
        verified=False,
    ),
}


def model_from_firmware(firmware: str) -> Model | None:
    """Model from the firmware string the panel reports, e.g. '6.08 01050'."""
    parts = firmware.split()
    if len(parts) < 2:
        return None
    return MODELS.get(parts[1].lstrip("0"))
