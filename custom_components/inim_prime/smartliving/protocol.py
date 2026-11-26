"""TCP connection to the SmartLAN board, read requests and commands.

A read request is 8 bytes:

    00 | bank | address (3 bytes, big endian) | 00 | length | checksum

checksum = sum of the first 7 bytes, modulo 256. The panel answers with
`length` bytes followed by their own checksum.

A command is an 8-byte write header (first byte 01) followed right away by
its payload; the panel acknowledges with the checksum of the payload, then
the result is read at 0x2004. Only the addresses in COMMANDS, each with its
fixed payload length, can be written.
"""
from __future__ import annotations

import asyncio
import logging

from .const import (
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    COMMAND_RESULT_ADDRESS,
    COMMAND_RESULT_LENGTH,
    COMMANDS,
    MAX_READ,
    PASSWORD_DELAY,
    READ,
    WRITE,
)

_LOGGER = logging.getLogger(__name__)


class SmartLivingError(Exception):
    """Base error of this library."""


class SmartLivingConnectionError(SmartLivingError):
    """The panel could not be reached or closed the connection.

    With the TCP password enabled on the SmartLAN board and a wrong or missing
    password, the panel closes the connection at the first request.
    """


class SmartLivingChecksumError(SmartLivingError):
    """An answer did not match its checksum."""


class SmartLivingCommandError(SmartLivingError):
    """The panel did not acknowledge a command or refused it."""


def checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def read_request(bank: int, address: int, length: int) -> bytes:
    """Build a read request. Raises ValueError outside the safe range."""
    if bank not in (0x00, 0x01):
        raise ValueError(f"unknown bank {bank:#04x}")
    if not 0 <= address <= 0xFFFFFF:
        raise ValueError(f"address {address:#x} out of range")
    if not 1 <= length <= MAX_READ:
        raise ValueError(f"length {length} out of range 1-{MAX_READ}")
    frame = bytes([READ, bank]) + address.to_bytes(3, "big") + bytes([0x00, length])
    return frame + bytes([checksum(frame)])


def write_request(address: int, payload: bytes) -> bytes:
    """Build a command: write header + payload. Only known commands."""
    expected = COMMANDS.get(address)
    if expected is None:
        raise ValueError(f"{address:#x} is not a known command address")
    if len(payload) != expected:
        raise ValueError(f"command {address:#x} takes {expected} bytes, got {len(payload)}")
    header = bytes([WRITE, 0x00]) + address.to_bytes(3, "big") + bytes([0x00, len(payload)])
    return header + bytes([checksum(header)]) + payload


class Protocol:
    """One TCP connection to the SmartLAN board, serialised by a lock.

    The board accepts a single client at a time: while this connection is open
    the installer software and apps that connect locally cannot.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        password: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._host = host
        self._port = port
        self._password = password or None
        self._timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        await self.close()
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), self._timeout
            )
        except (OSError, TimeoutError) as err:
            raise SmartLivingConnectionError(
                f"cannot connect to {self._host}:{self._port}: {err}"
            ) from err
        if self._password:
            # Sent as plain ASCII right after connecting.
            self._writer.write(self._password.encode("ascii"))
            await self._writer.drain()
            await asyncio.sleep(PASSWORD_DELAY)

    async def close(self) -> None:
        writer, self._reader, self._writer = self._writer, None, None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _exchange(self, request: bytes, answer_length: int) -> bytes:
        if not self.connected:
            await self.connect()
        assert self._reader is not None and self._writer is not None
        try:
            self._writer.write(request)
            await self._writer.drain()
            return await asyncio.wait_for(
                self._reader.readexactly(answer_length), self._timeout
            )
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as err:
            await self.close()
            raise SmartLivingConnectionError(f"no answer from the panel: {err!r}") from err

    async def read(self, bank: int, address: int, length: int) -> bytes:
        """Read `length` bytes (any size) starting at `address` in `bank`."""
        data = bytearray()
        async with self._lock:
            while len(data) < length:
                size = min(MAX_READ, length - len(data))
                part_address = address + len(data)
                raw = await self._exchange(read_request(bank, part_address, size), size + 1)
                payload, received = raw[:-1], raw[-1]
                if checksum(payload) != received:
                    raise SmartLivingChecksumError(
                        f"bad checksum at {bank:02x}:{part_address:06x}"
                    )
                data += payload
        return bytes(data)

    async def command(self, address: int, payload: bytes) -> int:
        """Send a command and return the result byte read at 0x2004.

        Never retried: if the acknowledgement does not come, the connection
        is closed so nothing else sent later can be taken as command data.
        """
        request = write_request(address, payload)
        async with self._lock:
            ack = await self._exchange(request, 1)
            if ack[0] != checksum(payload):
                # Seen on some panels (ack 0x00): the result below still tells.
                _LOGGER.debug("command %#x: ack %#04x, expected %#04x", address, ack[0], checksum(payload))
            raw = await self._exchange(
                read_request(0x00, COMMAND_RESULT_ADDRESS, COMMAND_RESULT_LENGTH),
                COMMAND_RESULT_LENGTH + 1,
            )
        return raw[0]
