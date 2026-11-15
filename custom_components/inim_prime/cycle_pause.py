"""Experimental: keep the Native connection closed across the panel's cycle.

The panel was seen closing its network connections at instants that fall on
a fixed cadence (PERIODIC_DROP_PHASE + k * PERIODIC_DROP_PERIOD, Unix time),
and only when a local connection is open at that moment. With this option
the Native connection is closed a few seconds before each instant and
reopened a few seconds after. Only Native: PrimeLAN is never touched.

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
from .const import (
    NATIVE_CYCLE_PAUSE_AFTER_DEFAULT,
    NATIVE_CYCLE_PAUSE_BEFORE_DEFAULT,
    NATIVE_CYCLE_PAUSE_CLOSE_WHEN_FREE,
    NATIVE_CYCLE_PAUSE_MAX,
    NATIVE_CYCLE_PAUSE_MIN,
    PERIODIC_DROP_PERIOD,
    PERIODIC_DROP_PHASE,
)
from .flow.const import OptionsKey

_LOGGER = logging.getLogger(__name__)


def next_cycle_instant(wall_time: float) -> float:
    """First instant of the cadence that comes after wall_time (Unix time)."""
    cycles = math.floor((wall_time - PERIODIC_DROP_PHASE) / PERIODIC_DROP_PERIOD) + 1
    return PERIODIC_DROP_PHASE + cycles * PERIODIC_DROP_PERIOD


def _margin(value, default: float) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(seconds, NATIVE_CYCLE_PAUSE_MIN), NATIVE_CYCLE_PAUSE_MAX)


async def _sleep_until(wall_time: float) -> None:
    # In short steps on the wall clock: the instants are wall-clock times and
    # a clock adjustment during a long sleep would shift the pause.
    while (remaining := wall_time - time.time()) > 0:
        await asyncio.sleep(min(remaining, 30.0))


# A close this much later than planned is reported (seconds).
_LATE_CLOSE = 0.15


async def run_cycle_pause(adapter: NativeAdapter, before: float, after: float) -> None:
    """Pause the Native connection around every instant of the cadence.

    The connection is closed exactly `before` seconds ahead of the instant
    and reopened exactly `after` seconds past it, on any installation: how
    early the requests have to stop for that is measured on the panel."""
    while True:
        lead = adapter.request_lead
        instant = next_cycle_instant(time.time() + before + lead)
        await _sleep_until(instant - before - lead)
        # measured again now: it is what this pause really uses
        lead = min(adapter.request_lead, max(instant - before - time.time(), 0.0))
        if not adapter.can_pause:
            _LOGGER.debug("Cycle pause skipped at this cycle: the Native connection is not open")
            await _sleep_until(instant + after + 1.0)
            continue
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
                    "panel's cycle (at the latest %.1fs), no new request from %.2fs "
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
        # never the same instant twice, whatever the margins
        await _sleep_until(instant + after + 1.0)


def _longest(adapter: NativeAdapter) -> str:
    """The longest measured request of each kind, the slowest first."""
    by_kind = adapter.longest_request_by_kind
    if not by_kind:
        return "none yet"
    return ", ".join(f"{kind} {duration:.2f}s" for kind, duration in by_kind.items())


def setup_cycle_pause(hass: HomeAssistant, entry: ConfigEntry, adapter: NativeAdapter | None) -> None:
    """Start the pause task when the option is on and Native is configured."""
    options = entry.options
    if adapter is None or not options.get(OptionsKey.NATIVE_CYCLE_PAUSE):
        return
    before = _margin(options.get(OptionsKey.NATIVE_CYCLE_PAUSE_BEFORE), NATIVE_CYCLE_PAUSE_BEFORE_DEFAULT)
    after = _margin(options.get(OptionsKey.NATIVE_CYCLE_PAUSE_AFTER), NATIVE_CYCLE_PAUSE_AFTER_DEFAULT)
    _LOGGER.debug(
        "Cycle pause enabled: Native connection closed from %.1fs before to "
        "%.1fs after each cycle of the panel", before, after,
    )
    task = entry.async_create_background_task(
        hass, run_cycle_pause(adapter, before, after), "inim_prime cycle pause",
    )

    def _stop() -> None:
        # returns nothing: Home Assistant runs what an unload callback
        # returns as a coroutine, and task.cancel() returns True
        task.cancel()

    entry.async_on_unload(_stop)
