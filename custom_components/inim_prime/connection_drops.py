"""Action listing the past connection drops, with start time and duration.

Nothing new is stored: the two connectivity sensors already write the last
drop in their 'ultima_caduta' / 'durata_ultima_caduta' attributes, and every
value they had is in the recorder. The action reads that history back.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .adapters.native_adapter import matches_periodic_drop_cadence
from .const import DOMAIN

SERVICE_CONNECTION_DROPS = "connection_drops"
MAX_DAYS = 365
# The history is read one slice at a time: the sensors write a row a minute,
# so a year in one query would load about a million states at once.
_SLICE = timedelta(days = 1)

CONNECTION_DROPS_SCHEMA = vol.Schema({
    vol.Optional("days", default = 1): vol.All(vol.Coerce(int), vol.Range(min = 1, max = MAX_DAYS)),
})

# unique_id suffix of the connectivity sensors -> name shown in the answer
_CONNECTIONS = {
    "_connectivity_native": "Native",
    "_connectivity_primelan": "PrimeLAN",
}


def _connectivity_entities(hass: HomeAssistant) -> dict[str, str]:
    """entity_id -> connection name, for the connectivity sensors of every panel."""
    found: dict[str, str] = {}
    for entry in er.async_get(hass).entities.values():
        if entry.platform != DOMAIN or entry.domain != "binary_sensor":
            continue
        for suffix, name in _CONNECTIONS.items():
            if entry.unique_id.endswith(suffix):
                found[entry.entity_id] = name
    return found


def extract_drops(states: Iterable[Any], connection: str) -> list[dict[str, Any]]:
    """Distinct drops found in the recorded states of one connectivity sensor."""
    drops: dict[str, dict[str, Any]] = {}
    for state in states:
        attributes = getattr(state, "attributes", None) or {}
        started = attributes.get("ultima_caduta")
        if not isinstance(started, str):
            continue
        start = dt_util.parse_datetime(started)
        if start is None:
            continue
        duration = attributes.get("durata_ultima_caduta")
        drops[started] = {
            "connessione": connection,
            "inizio": dt_util.as_local(start).isoformat(timespec = "milliseconds"),
            "durata_secondi": duration if isinstance(duration, (int, float)) else None,
            # on the panel's own periodic restart of its network connections
            "periodica": matches_periodic_drop_cadence(start.timestamp()),
            "_start": start,
        }
    return list(drops.values())


async def _connection_drops(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    if "recorder" not in hass.config.components:
        raise ServiceValidationError(
            "The recorder integration is not running: the past drops are read from its history"
        )
    from homeassistant.components.recorder import get_instance, history

    entities = _connectivity_entities(hass)
    end = dt_util.utcnow()
    start = end - timedelta(days = call.data.get("days", 1))

    found: dict[tuple[str, str], dict[str, Any]] = {}
    slice_start = start
    while entities and slice_start < end:
        slice_end = min(slice_start + _SLICE, end)
        recorded = await get_instance(hass).async_add_executor_job(
            # every recorded row, attribute-only updates included
            lambda s = slice_start, e = slice_end: history.get_significant_states(
                hass, s, e, list(entities),
                include_start_time_state = slice_start == start,
                significant_changes_only = False,
            )
        )
        for entity_id, connection in entities.items():
            for drop in extract_drops(recorded.get(entity_id, []), connection):
                found[(connection, drop["inizio"])] = drop
        slice_start = slice_end

    drops = [drop for drop in found.values() if drop["_start"] >= start]
    drops.sort(key = lambda drop: drop["_start"], reverse = True)
    for drop in drops:
        del drop["_start"]
    return {
        "dal": dt_util.as_local(start).isoformat(timespec = "seconds"),
        "al": dt_util.as_local(end).isoformat(timespec = "seconds"),
        "totale": len(drops),
        "periodiche": _count([drop for drop in drops if drop["periodica"]]),
        "altre": _count([drop for drop in drops if not drop["periodica"]]),
        "cadute": drops,
    }


def _count(drops: list[dict[str, Any]]) -> int | str:
    """How many, then how many per connection: 0, or "3 (Native 2, PrimeLAN 1)"."""
    if not drops:
        return 0
    per_connection = [
        f"{name} {count}" for name in _CONNECTIONS.values()
        if (count := sum(drop["connessione"] == name for drop in drops))
    ]
    return f"{len(drops)} ({', '.join(per_connection)})"


def async_register(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_CONNECTION_DROPS):
        return

    async def _handler(call: ServiceCall) -> ServiceResponse:
        return await _connection_drops(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_CONNECTION_DROPS, _handler,
        schema = CONNECTION_DROPS_SCHEMA, supports_response = SupportsResponse.ONLY,
    )
