"""Native scenarios, system status and event log: payloads and decoding.

Pure functions (no I/O), used by NativeAdapter. The commands are the ones the
Inim app sends to the panel in local mode; the event texts and the names come
from the panel's own tables, so the log reads like PrimeLAN's.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..const import (
    NATIVE_FAULTS_OFFSET,
    NATIVE_MASTER_PIN,
    NATIVE_SCENARIOS_MASK_OFFSET,
    NATIVE_SCENARIOS_MASK_SIZE,
    NATIVE_SCENARIOS_OPERATION,
    NATIVE_SCENARIOS_PARTITION_MASK,
    NATIVE_SCENARIO_ACTIVATE_OPERATION,
    NATIVE_SCENARIO_PREPARE_OPERATION,
    NATIVE_SUPPLY_VOLTAGE_OFFSET,
    NATIVE_EVENT_TYPE_SIZE,
)

_EPOCH = datetime(2000, 1, 1)
_LABEL_SIZE = 16


def _op(operation: int) -> bytes:
    return operation.to_bytes(4, "little")


# ── Scenarios ───────────────────────────────────────────────────────────────
def scenarios_payload(programmed: bool) -> bytes:
    """Active scenarios (programmed=False) or programmed ones (True)."""
    return (
        _op(NATIVE_SCENARIOS_OPERATION) + NATIVE_SCENARIOS_PARTITION_MASK
        + NATIVE_MASTER_PIN + bytes([1 if programmed else 0])
    )


def parse_scenario_mask(response: bytes) -> frozenset[int]:
    """0-based scenario ids set in the answer's bitmask."""
    raw = response[NATIVE_SCENARIOS_MASK_OFFSET:NATIVE_SCENARIOS_MASK_OFFSET + NATIVE_SCENARIOS_MASK_SIZE]
    mask = int.from_bytes(raw, "little")
    return frozenset(i for i in range(len(raw) * 8) if mask >> i & 1)


def scenario_prepare_payload(scenario_id: int) -> bytes:
    return _op(NATIVE_SCENARIO_PREPARE_OPERATION) + bytes(16) + bytes([scenario_id, 1]) + NATIVE_MASTER_PIN


def scenario_activate_payload(scenario_id: int) -> bytes:
    return _op(NATIVE_SCENARIO_ACTIVATE_OPERATION) + scenario_id.to_bytes(2, "little") + NATIVE_MASTER_PIN


# ── System status ───────────────────────────────────────────────────────────
def command_payload(operation: int) -> bytes:
    """A command with no data (system status, event log info and tables)."""
    return _op(operation) + b"\x00"


def parse_system_status(response: bytes) -> tuple[int, float | None]:
    """(fault bits, supply voltage): the bits are PrimeLAN's SystemFault ones."""
    faults = int.from_bytes(response[NATIVE_FAULTS_OFFSET:NATIVE_FAULTS_OFFSET + 2], "little")
    raw = response[NATIVE_SUPPLY_VOLTAGE_OFFSET:NATIVE_SUPPLY_VOLTAGE_OFFSET + 2]
    voltage = int.from_bytes(raw, "little") / 10 if len(raw) == 2 else None
    return faults, voltage


# ── Event log ───────────────────────────────────────────────────────────────
@dataclass(frozen = True)
class LogInfo:
    next_index: int
    record_size: int
    address: int
    capacity: int


def parse_log_info(response: bytes) -> LogInfo:
    data = response[4:]
    return LogInfo(
        next_index = int.from_bytes(data[0:2], "little"),
        record_size = int.from_bytes(data[4:6], "little"),
        address = int.from_bytes(data[6:10], "little"),
        capacity = int.from_bytes(data[10:14], "little"),
    )


def parse_log_tables(response: bytes) -> tuple[int, int]:
    """(address, size) of the event type table."""
    data = response[4:]
    return int.from_bytes(data[0:4], "little"), int.from_bytes(data[4:8], "little")


@dataclass(frozen = True)
class EventType:
    start: int      # first event code of this type
    count: int      # codes in the range (one per zone, area, user...)
    flags: int      # 0x80: the record says who/where (0x01 user, 0x10 keypad)
    kind: int       # what the code offset points to (zone, area, user...)
    text: str       # the panel's 16-character text


def parse_event_types(table: bytes, texts: bytes) -> list[EventType]:
    entries = [
        table[o:o + NATIVE_EVENT_TYPE_SIZE]
        for o in range(0, len(table) - NATIVE_EVENT_TYPE_SIZE + 1, NATIVE_EVENT_TYPE_SIZE)
    ]
    types = []
    for i, entry in enumerate(entries):
        start = int.from_bytes(entry[0:2], "little")
        end = int.from_bytes(entries[i + 1][0:2], "little") if i + 1 < len(entries) else start + 1
        raw = texts[i * _LABEL_SIZE:(i + 1) * _LABEL_SIZE]
        text = raw.split(b"\x00", 1)[0].decode("latin-1")
        types.append(EventType(start, max(end - start, 1), entry[2], entry[3], text))
    return types


def find_event_type(types: list[EventType], code: int) -> tuple[EventType, int] | None:
    """The type of an event code and the code offset (the object index)."""
    for event_type in reversed(types):
        if event_type.start <= code:
            offset = code - event_type.start
            return (event_type, offset) if offset < event_type.count else None
    return None


@dataclass(frozen = True)
class LogRecord:
    index: int
    timestamp: datetime     # panel local time
    code: int
    tail: bytes             # user, flags, keypad, ...


def record_indexes(info: LogInfo, count: int) -> list[int]:
    """Indexes of the newest `count` records, oldest first."""
    count = min(count, info.capacity)
    newest = (info.next_index - 1) % info.capacity
    return [(newest - back) % info.capacity for back in range(count - 1, -1, -1)]


def parse_record(index: int, raw: bytes) -> LogRecord | None:
    if len(raw) < 14 or raw[0:4] in (b"\x00\x00\x00\x00", b"\xff\xff\xff\xff"):
        return None
    seconds = int.from_bytes(raw[0:4], "little")
    return LogRecord(
        index = index,
        timestamp = _EPOCH + timedelta(seconds = seconds),
        code = int.from_bytes(raw[8:10], "little"),
        tail = bytes(raw[10:14]),
    )


def is_restore(record: LogRecord) -> bool:
    """The end of a condition ("Fine ..."): bit 7 of the second tail byte clear."""
    return not record.tail[1] & 0x80


# Who/where of an event, from its type flags and the record tail.
USER_FLAG = 0x01
KEYPAD_FLAG = 0x10
DETAILS_FLAG = 0x80
REMOTE_SOURCES = (0xFE, 0xFF)
NO_USER = 0xFF  # no user code (keypad quick arming, Home Assistant)


def record_user(event_type: EventType, record: LogRecord) -> int | None:
    if event_type.flags & DETAILS_FLAG and event_type.flags & USER_FLAG:
        return record.tail[0]
    return None


def record_source(event_type: EventType, record: LogRecord) -> int | None:
    """Keypad index, or -1 for a remote command (app, Home Assistant)."""
    if event_type.flags & DETAILS_FLAG and event_type.flags & KEYPAD_FLAG:
        return -1 if record.tail[2] in REMOTE_SOURCES else record.tail[2]
    return None


def contiguous_runs(indexes: list[int]) -> list[tuple[int, int]]:
    """(first index, count) runs, so each run is read in one go."""
    runs: list[tuple[int, int]] = []
    for index in sorted(indexes):
        if runs and runs[-1][0] + runs[-1][1] == index:
            runs[-1] = (runs[-1][0], runs[-1][1] + 1)
        else:
            runs.append((index, 1))
    return runs
