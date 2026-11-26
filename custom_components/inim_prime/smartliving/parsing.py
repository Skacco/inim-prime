"""Pure decoding of what the panel returns (no I/O)."""
from __future__ import annotations

from .const import (
    FREE,
    NAME_LENGTH,
    TERMINAL_OUTPUT_TYPES,
    TERMINAL_RECORD_SIZE,
    TERMINAL_RECORD_TYPE_OFFSET,
    ZONE_RECORD_SIZE,
)
from .models import AreaState, AreaStatus, PanelInfo, ZoneState, ZoneStatus


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


def parse_area_status(raw: bytes, areas: int) -> dict[int, AreaStatus]:
    """One nibble per area, low nibble first; alarm memory bitmask from byte 10."""
    memory = int.from_bytes(raw[10:12], "little") if len(raw) >= 12 else (raw[10] if len(raw) > 10 else 0)
    result = {}
    for area in range(1, areas + 1):
        byte = raw[(area - 1) // 2]
        value = byte & 0x0F if area % 2 else byte >> 4
        try:
            state = AreaState(value)
        except ValueError:
            state = AreaState.UNKNOWN
        result[area] = AreaStatus(state=state, raw=value, alarm_memory=bool(memory >> (area - 1) & 1))
    return result


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
