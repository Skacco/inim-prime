"""Payloads of the panel commands. Pure functions, no I/O."""
from __future__ import annotations

from enum import IntEnum

from .const import PIN_LENGTH

AREA_BLOCK_LENGTH = 8  # 16 areas, one nibble each
MAX_AREAS = AREA_BLOCK_LENGTH * 2
PIN_FILLER = 0xFF


class ArmMode(IntEnum):
    """Nibble written for an area. KEEP leaves it as it is."""

    KEEP = 0
    AWAY = 1
    STAY = 2
    INSTANT = 3
    DISARM = 4


def encode_pin(pin: str) -> bytes:
    """'1234' -> 01 02 03 04 FF FF."""
    if not pin.isdigit() or not 4 <= len(pin) <= PIN_LENGTH:
        raise ValueError("the PIN must be 4 to 6 digits")
    return bytes(int(d) for d in pin) + bytes([PIN_FILLER] * (PIN_LENGTH - len(pin)))


def arm_payload(pin: str, modes: dict[int, ArmMode]) -> bytes:
    """PIN + one nibble per area (area 1 = low nibble of the first byte)."""
    if not modes:
        raise ValueError("no area given")
    block = bytearray(AREA_BLOCK_LENGTH)
    for area, mode in modes.items():
        if not 1 <= area <= MAX_AREAS:
            raise ValueError(f"area {area} out of range")
        index = (area - 1) // 2
        if area % 2:
            block[index] = (block[index] & 0xF0) | int(mode)
        else:
            block[index] = (block[index] & 0x0F) | (int(mode) << 4)
    return encode_pin(pin) + bytes(block)


def reset_memory_payload(pin: str, areas: set[int]) -> bytes:
    """PIN + area bitmask, 2 bytes little endian."""
    if not areas or not all(1 <= a <= MAX_AREAS for a in areas):
        raise ValueError("areas out of range")
    mask = sum(1 << (a - 1) for a in areas)
    return encode_pin(pin) + mask.to_bytes(2, "little")


def exclude_payload(pin: str, physical_index: int, excluded: bool) -> bytes:
    """PIN + physical index of the zone (2 bytes LE) + 1 to exclude, 0 to include."""
    if not 0 <= physical_index <= 0xFFFF:
        raise ValueError("physical index out of range")
    return encode_pin(pin) + physical_index.to_bytes(2, "little") + bytes([1 if excluded else 0])


def scenario_payload(pin: str, scenario: int) -> bytes:
    """PIN + scenario number (1-based here, 0-based on the wire)."""
    if not 1 <= scenario <= 30:
        raise ValueError("scenario out of range 1-30")
    return encode_pin(pin) + bytes([scenario - 1])
