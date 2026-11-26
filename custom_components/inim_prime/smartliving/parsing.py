"""Pure decoding of what the panel returns (no I/O)."""
from __future__ import annotations

from .const import (
    AREA_ALARM_BITMAP,
    AREA_MEMORY_BITMAP,
    FREE,
    NAME_LENGTH,
    TERMINAL_OUTPUT_TYPES,
    TERMINAL_RECORD_SIZE,
    TERMINAL_RECORD_TYPE_OFFSET,
    ZONE_RECORD_SIZE,
)
from datetime import datetime, timedelta

from .models import AreaState, AreaStatus, Event, PanelInfo, Scenario, ZoneState, ZoneStatus


def decode_text(raw: bytes) -> str:
    return raw.decode("cp1252", errors="replace").replace("\x00", " ").replace("\xff", " ").strip()


def parse_panel_info(raw: bytes) -> PanelInfo:
    text = raw.decode("ascii", errors="replace").strip()
    parts = text.split()
    firmware = parts[0] if parts else ""
    model = parts[1].lstrip("0") if len(parts) > 1 else ""
    return PanelInfo(firmware=firmware, model=model, raw=text)


def parse_names(raw: bytes, count: int) -> dict[int, str]:
    """16-byte names, 1-based."""
    return {
        i + 1: decode_text(raw[i * NAME_LENGTH:(i + 1) * NAME_LENGTH])
        for i in range(min(count, len(raw) // NAME_LENGTH))
    }


def parse_terminal_table(raw: bytes) -> dict[int, int]:
    """Logical terminal (1-based) -> physical index. 0xFF = free position."""
    table: dict[int, int] = {}
    for physical_index, value in enumerate(raw):
        if value != FREE:
            table.setdefault(value + 1, physical_index)
    return table


def parse_terminal_types(raw: bytes, count: int) -> dict[int, int]:
    """Logical terminal (1-based) -> type byte of its 12-byte record."""
    types = {}
    for i in range(min(count, len(raw) // TERMINAL_RECORD_SIZE)):
        types[i + 1] = raw[i * TERMINAL_RECORD_SIZE + TERMINAL_RECORD_TYPE_OFFSET]
    return types


def is_output_type(terminal_type: int) -> bool:
    return terminal_type in TERMINAL_OUTPUT_TYPES


def parse_zone_areas(raw: bytes, count: int, areas: int) -> dict[int, frozenset[int]]:
    """Logical terminal (1-based) -> areas (1-based) from the 9-byte records."""
    result = {}
    for i in range(min(count, len(raw) // ZONE_RECORD_SIZE)):
        offset = i * ZONE_RECORD_SIZE
        mask = raw[offset] | (raw[offset + 1] << 8)
        result[i + 1] = frozenset(a + 1 for a in range(areas) if mask >> a & 1)
    return result


def area_status_length(areas: int) -> int:
    return areas // 2 + 1 + 10


def parse_area_status(raw: bytes, areas: int) -> dict[int, AreaStatus]:
    """One nibble per area (low nibble first), then 2-byte area bitmaps:
    the first is the alarm in progress, the third the alarm memory."""
    base = areas // 2 + 1

    def bitmap(index: int) -> int:
        start = base + 2 * index
        return int.from_bytes(raw[start:start + 2], "little") if len(raw) >= start + 2 else 0

    alarm = bitmap(AREA_ALARM_BITMAP)
    memory = bitmap(AREA_MEMORY_BITMAP)
    result = {}
    for area in range(1, areas + 1):
        byte = raw[(area - 1) // 2]
        value = byte & 0x0F if area % 2 else byte >> 4
        try:
            state = AreaState(value)
        except ValueError:
            state = AreaState.UNKNOWN
        result[area] = AreaStatus(
            state=state,
            raw=value,
            alarm_memory=bool(memory >> (area - 1) & 1),
            alarm=bool(alarm >> (area - 1) & 1),
        )
    return result


def parse_scenarios(
    raw: bytes, record_size: int, names: dict[int, str], areas: int
) -> dict[int, Scenario]:
    """Scenarios with at least one area to set: one nibble per area."""
    result = {}
    for scenario_id, name in names.items():
        record = raw[(scenario_id - 1) * record_size:scenario_id * record_size]
        if len(record) < record_size:
            break
        modes = {}
        for area in range(1, areas + 1):
            byte = record[(area - 1) // 2]
            value = byte & 0x0F if area % 2 else byte >> 4
            if value:
                modes[area] = value
        if modes:
            result[scenario_id] = Scenario(scenario_id, name, modes)
    return result


def active_scenarios(scenarios: dict[int, Scenario], areas: dict[int, AreaStatus]) -> frozenset[int]:
    """A scenario is active when every area it sets is in that mode."""
    return frozenset(
        sid for sid, scenario in scenarios.items()
        if all(areas.get(a) is not None and areas[a].raw == m for a, m in scenario.modes.items())
    )


def _bit(raw: bytes, index: int) -> bool:
    return bool(raw[index // 8] >> (index % 8) & 1) if index // 8 < len(raw) else False


def parse_zone_status(
    states: bytes, enabled: bytes, alarm_memory: bytes, zones: list[int]
) -> dict[int, ZoneStatus]:
    """Two bits per zone (low bits first), one bit per zone in the masks.

    In the enabled mask a set bit means the zone is included; a cleared bit
    means it is excluded.
    """
    result = {}
    for zone in zones:
        index = zone - 1
        byte = states[index // 4] if index // 4 < len(states) else 0
        value = byte >> (2 * (index % 4)) & 0x03
        try:
            state = ZoneState(value)
        except ValueError:
            state = ZoneState.UNKNOWN
        result[zone] = ZoneStatus(
            state=state,
            raw=value,
            enabled=_bit(enabled, index),
            alarm_memory=_bit(alarm_memory, index),
        )
    return result


def parse_serial(raw: bytes) -> str | None:
    """16 ASCII digits, or None when the field is empty or not digits."""
    text = raw.decode("ascii", errors="replace").strip()
    return text if text.isdigit() else None


def output_state_layout(zones: int, fixed: int) -> tuple[int, int]:
    """(first byte, total length) of the output states in the 0x2003 block:
    after the zone alarm memory, one bit per logical terminal then the fixed
    outputs (relay, OC1, OC2)."""
    base = 2 * zones // 8 + 1 + zones // 8 + 1
    return base, base + (zones + fixed + 7) // 8


def parse_output_states(raw: bytes, zones: int, fixed: int, bits: dict[int, int]) -> dict[int, bool]:
    """Output id -> on, from the 0x2003 block; `bits` maps output id -> state bit."""
    base, length = output_state_layout(zones, fixed)
    block = raw[base:length]
    return {
        output_id: bool(block[bit // 8] >> (bit % 8) & 1) if bit // 8 < len(block) else False
        for output_id, bit in bits.items()
    }


def parse_name_pointer(raw: bytes, offset: int) -> int:
    return raw[offset] | (raw[offset + 1] << 8) if len(raw) > offset + 1 else 0


_EPOCH = datetime(2000, 1, 1)
_AREA_EVENTS = {0x8A, 0x8C, 0x8D, 0x0A, 0x0C, 0x95, 0x96}
_ZONE_EVENTS = {0x80, 0x00, 0x85, 0x05}
SCENARIO_EVENT = 0x9E
KNOWN_EVENT_CODES = _AREA_EVENTS | _ZONE_EVENTS | {SCENARIO_EVENT, 0xBA, 0xC2, 0x42, 0x9B, 0x1B}


def event_address(pointer: int, back: int, base: int, length: int) -> int | None:
    """Names-bank address of the event `back` steps before the newest one."""
    newest = ((pointer - 1) * length + base) & 0xFFFF
    position = newest - back * length
    return position if position >= 0 else None


def parse_event(raw: bytes, index: int, areas: int) -> Event:
    seconds = int.from_bytes(raw[0:4], "little")
    d1, d2, d3, _device, code = raw[4:9]
    mask = d1 | (d2 << 8)
    event_areas = frozenset(a + 1 for a in range(areas) if mask >> a & 1)
    zone = scenario = user = None
    if code in _ZONE_EVENTS:
        zone = d3 + 1
    elif code == SCENARIO_EVENT:
        scenario = d3 + 1
        event_areas = frozenset()
    elif code in _AREA_EVENTS:
        user = d3
    else:
        event_areas = frozenset()
    return Event(
        index=index,
        timestamp=_EPOCH + timedelta(seconds=seconds),
        code=code,
        areas=event_areas,
        zone=zone,
        scenario=scenario,
        user=user,
        raw=bytes(raw),
    )


def plausible_events(events: list[Event], now: datetime) -> bool:
    """True when the events look like a real log: known types and dates
    between 2005 and tomorrow. Used where the log address is not verified."""
    if not events:
        return False
    low, high = datetime(2005, 1, 1), now + timedelta(days=1)
    return all(e.code in KNOWN_EVENT_CODES and low <= e.timestamp <= high for e in events)
