from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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
    """Two-bit zone state in the live status. 1 and 2 seen on real panels;
    3 is the tamper of a double-balanced line (cut or shorted)."""

    STANDBY = 1
    OPEN = 2
    TAMPER = 3
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
    output_id: int                  # terminal outputs: logical terminal (1-based);
                                    # fixed outputs: zones + 1 + j (relay, OC1, OC2)
    name: str
    physical_index: int             # command index
    state_bit: int = 0              # bit in the output state block
    fixed: bool = False


@dataclass(frozen=True)
class Event:
    index: int                      # position in the panel log
    timestamp: datetime             # panel time
    code: int
    areas: frozenset[int]
    zone: int | None                # 1-based, for zone events
    scenario: int | None            # 1-based, for scenario events
    user: int | None                # code or key number, for arm/disarm events
    raw: bytes


@dataclass(frozen=True)
class Scenario:
    scenario_id: int                # 1-based, as on the keypad
    name: str
    modes: dict[int, int]           # area (1-based) -> arm mode nibble (1-4)


@dataclass(frozen=True)
class Configuration:
    """What the panel is programmed with: read once at setup."""

    info: PanelInfo
    area_names: dict[int, str]
    scenario_names: dict[int, str]
    zones: dict[int, Zone]
    outputs: dict[int, Output]
    scenarios: dict[int, Scenario] = field(default_factory=dict)  # programmed only


@dataclass(frozen=True)
class AreaStatus:
    state: AreaState
    raw: int
    alarm_memory: bool
    alarm: bool = False              # alarm in progress


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
    active_scenarios: frozenset[int] = frozenset()
    outputs: dict[int, bool] = field(default_factory=dict)
