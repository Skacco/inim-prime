"""Keep the Native connection closed across the panel's periodic cycle.

The panel was seen closing its network connections at instants that fall on
a fixed cadence (PERIODIC_DROP_PHASE + k * PERIODIC_DROP_PERIOD, Unix time),
and only when a local connection is open at that moment. With Native, the
connection is closed shortly before each instant and reopened shortly after,
always. With PrimeLAN too, its requests are held and its connections
closed over the same span.

During the pause the entities keep their last value and the requests wait
for the connection to come back; the pause is not recorded as a drop.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .adapters.native_adapter import NativeAdapter
from .adapters.primelan_adapter import PrimelanAdapter
from .const import (
    NATIVE_CYCLE_PAUSE_AFTER,
    NATIVE_CYCLE_PAUSE_BEFORE,
    NATIVE_CYCLE_PAUSE_CLOSE_WHEN_FREE,
    NATIVE_CYCLE_PAUSE_LEAD_MAX,
    PERIODIC_DROP_PERIOD,
    PERIODIC_DROP_PHASE,
)

_LOGGER = logging.getLogger(__name__)


def next_cycle_instant(wall_time: float) -> float:
    """First instant of the cadence that comes after wall_time (Unix time)."""
    cycles = math.floor((wall_time - PERIODIC_DROP_PHASE) / PERIODIC_DROP_PERIOD) + 1
    return PERIODIC_DROP_PHASE + cycles * PERIODIC_DROP_PERIOD


async def _sleep_until(wall_time: float) -> None:
    # In short steps on the wall clock: the instants are wall-clock times and
    # a clock adjustment during a long sleep would shift the pause.
    while (remaining := wall_time - time.time()) > 0:
        await asyncio.sleep(min(remaining, 30.0))


async def _sleep_until_requests_stop(adapter: NativeAdapter, close_at: float) -> float:
    """Wait until the requests have to stop for the connection to be free at
    close_at, and return the lead that was used.

    The lead comes from the request durations saved as they happen, read
    again at every wake-up: the one used is the one of that moment."""
    while (remaining := close_at - adapter.request_lead - time.time()) > 0:
        await asyncio.sleep(min(remaining, 30.0))
    return adapter.request_lead


# A close this much later than planned is reported (seconds).
_LATE_CLOSE = 0.15


async def run_cycle_pause(
        adapter: NativeAdapter, before: float, after: float,
        primelan: PrimelanAdapter | None = None,
) -> None:
    """Pause the connections around every instant of the cadence.

    The Native connection is closed exactly `before` seconds ahead of the
    instant and reopened exactly `after` seconds past it, on any
    installation: how early the requests have to stop for that is measured
    on the panel. With PrimeLAN too, before the close each PrimeLAN request
    starts only if its command, by its own measured duration, ends by the
    close (one at a time, the others wait for the reopening), and its
    connections are closed at the same time as the Native one: a PrimeLAN
    request open at the instant was seen restarting the panel's network as
    well."""
    while True:
        instant = next_cycle_instant(time.time() + before + NATIVE_CYCLE_PAUSE_LEAD_MAX + 1.0)
        primelan_task = None
        primelan_lead = 0.0
        if primelan is not None:
            # PrimeLAN's checks start by its slowest command, or by Native's
            # lead if that is longer: Native's wait below starts after this one
            primelan_lead = await _sleep_until_requests_stop(
                _Earliest(adapter, primelan), instant - before,
            )
            primelan_task = asyncio.create_task(
                primelan.pause(close_at = instant - before, latest = instant - 0.1)
            )
        try:
            await _pause_native(adapter, instant, before, after)
        except asyncio.CancelledError:
            # unloading: let the held requests fail as the client is closed
            if primelan_task is not None:
                primelan_task.cancel()
                primelan.release()
            raise
        if primelan_task is not None:
            await _end_primelan_pause(primelan, primelan_task, instant, before, after, primelan_lead)
        # never the same instant twice, whatever the margins
        await _sleep_until(instant + after + 1.0)


async def _pause_native(adapter: NativeAdapter, instant: float, before: float, after: float) -> None:
    lead = await _sleep_until_requests_stop(adapter, instant - before)
    if not adapter.can_pause:
        _LOGGER.debug("Cycle pause skipped at this cycle: the Native connection is not open")
        await _sleep_until(instant + after + 1.0)
        return
    try:
        closed_at = await adapter.pause(
            close_at = instant - before,
            # late rather than never, but never past the instant
            idle_timeout = max(instant - 0.1 - time.time(), 0.1),
            close_when_free = NATIVE_CYCLE_PAUSE_CLOSE_WHEN_FREE,
        )
        if closed_at is None:
            _LOGGER.warning(
                "Cycle pause skipped at this cycle: a request was still "
                "running %.1fs after the others were stopped (longest "
                "measured: %s)",
                before + lead, _longest(adapter),
            )
        else:
            late = closed_at - (instant - before)
            _LOGGER.debug(
                "Cycle pause: Native connection closed %.2fs before the "
                "panel's cycle (planned %.1fs), no new request from %.2fs "
                "earlier (longest measured: %s), reopening %.1fs after it",
                instant - closed_at, before, lead,
                _longest(adapter), after,
            )
            if late > _LATE_CLOSE:
                _LOGGER.warning(
                    "Cycle pause: the Native connection was closed %.2fs "
                    "later than planned (%.2fs before the panel's cycle "
                    "instead of %.1fs), a request took longer than the "
                    "longest measured so far (%s)",
                    late, instant - closed_at, before,
                    _longest(adapter),
                )
            await _sleep_until(instant + after)
    finally:
        if adapter.paused:
            if await asyncio.shield(adapter.resume()):
                _LOGGER.debug(
                    "Cycle pause: Native connection reopened at once, the "
                    "panel did not restart its network at this cycle"
                )
            else:
                _LOGGER.debug(
                    "Cycle pause: the panel did not accept the connection "
                    "back, it restarted its network at this cycle all the same"
                )


class _Earliest:
    """The longer of the two leads, read at every wake-up."""

    def __init__(self, *adapters) -> None:
        self._adapters = adapters

    @property
    def request_lead(self) -> float:
        return max(adapter.request_lead for adapter in self._adapters)


async def _end_primelan_pause(
        primelan: PrimelanAdapter, task: asyncio.Task, instant: float, before: float, after: float,
        lead: float,
) -> None:
    """Reopen PrimeLAN at the same time as Native and report how it went."""
    await _sleep_until(instant + after)
    try:
        closed_at, in_flight = await task
    except asyncio.CancelledError:
        closed_at, in_flight = None, 0
    except Exception as err:  # noqa: BLE001 - closing the client failed
        _LOGGER.debug("Cycle pause: closing the PrimeLAN connections failed (%s)", err)
        closed_at, in_flight = None, 0
    postponed = primelan.postponed
    last_end = primelan.last_end
    reopened = await primelan.resume()
    if closed_at is not None:
        _LOGGER.debug(
            "Cycle pause: PrimeLAN connections closed %.2fs before the panel's "
            "cycle (planned %.1fs); checks from %.2fs earlier, last request "
            "ended %s, held for the reopening: %s (longest measured: %s), %s",
            instant - closed_at, before, lead,
            f"{closed_at - last_end:.2f}s before the close" if last_end is not None else "before the checks",
            ", ".join(postponed) or "none", _longest(primelan),
            "reopened" if reopened else "not reopened at once",
        )
        if closed_at > instant:
            _LOGGER.warning(
                "Cycle pause: a PrimeLAN request was still running at the "
                "panel's cycle (%d in flight when the connections were due to close)",
                in_flight,
            )


def _longest(adapter: NativeAdapter | PrimelanAdapter) -> str:
    """The usual longest request, then the longest of each kind."""
    by_kind = adapter.longest_request_by_kind
    if not by_kind:
        return "none yet"
    kinds = ", ".join(f"{kind} {duration:.2f}s" for kind, duration in by_kind.items())
    return f"98% within {adapter.typical_longest_request:.2f}s; {kinds}"


def setup_cycle_pause(
        hass: HomeAssistant, entry: ConfigEntry, adapter: NativeAdapter | None,
        primelan: PrimelanAdapter | None = None,
) -> None:
    """Start the pause task: always, when Native is configured (PrimeLAN, if
    configured too, is paused with it)."""
    if adapter is None:
        return
    before, after = NATIVE_CYCLE_PAUSE_BEFORE, NATIVE_CYCLE_PAUSE_AFTER
    _LOGGER.debug(
        "Cycle pause: Native connection closed from %.1fs before to %.1fs "
        "after each cycle of the panel%s", before, after,
        " (PrimeLAN too)" if primelan is not None else "",
    )
    task = entry.async_create_background_task(
        hass, run_cycle_pause(adapter, before, after, primelan), "inim_prime cycle pause",
    )

    def _stop() -> None:
        # returns nothing: Home Assistant runs what an unload callback
        # returns as a coroutine, and task.cancel() returns True
        task.cancel()

    entry.async_on_unload(_stop)
