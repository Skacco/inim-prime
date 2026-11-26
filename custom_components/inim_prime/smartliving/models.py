from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class AreaState(IntEnum):
    """Area state nibble in the live status."""

    NOT_CONFIGURED = 0
    AWAY = 1
    STAY = 2
    INSTANT = 3
    DISARMED = 4
    UNKNOWN = -1


class ZoneState(IntEnum):
    """Two-bit zone state in the live status (1 and 2 seen on real panels)."""

    STANDBY = 1
    OPEN = 2
    UNKNOWN = -1


@dataclass(frozen=True)
class PanelInfo:
    firmware: str   # e.g. "6.08"
    model: str      # e.g. "1050"
    raw: str        # e.g. "6.08 01050"
    serial: str | None = None


@dataclass(frozen=True)
class Zone:
    zone_id: int                    # 1-based, as on the keypad
    name: str
    physical_index: int             # position in the terminal table
    areas: frozenset[int] = field(default_factory=frozenset)  # 1-based


@dataclass(frozen=True)
class Output:
    output_id: int                  # logical terminal, 1-based
    name: str
    physical_index: int


@dataclass(frozen=True)
class Configuration:
    """What the panel is programmed with: read once at setup."""

    info: PanelInfo
    area_names: dict[int, str]
    scenario_names: dict[int, str]
    zones: dict[int, Zone]
    outputs: dict[int, Output]


@dataclass(frozen=True)
class AreaStatus:
    state: AreaState
    raw: int
    alarm_memory: bool


@dataclass(frozen=True)
class ZoneStatus:
    state: ZoneState
    raw: int
    enabled: bool                   # False = excluded (bypassed)
    alarm_memory: bool


@dataclass(frozen=True)
class Status:
    areas: dict[int, AreaStatus]
    zones: dict[int, ZoneStatus]
