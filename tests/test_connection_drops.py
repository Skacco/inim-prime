"""The 'connection_drops' action rebuilds the list of past drops from the
recorded history of the connectivity sensors."""
from types import SimpleNamespace

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.inim_prime import connection_drops as cd
from custom_components.inim_prime.const import DOMAIN


def _state(started, duration):
    return SimpleNamespace(attributes = {
        "ultimo_ok": "x", "ultima_caduta": started, "durata_ultima_caduta": duration,
    })


def test_extract_drops_keeps_one_entry_per_drop():
    states = [
        _state(None, None),                                    # after a restart
        _state("2026-11-13T11:12:12.855083+00:00", 8.0),
        _state("2026-11-13T11:12:12.855083+00:00", 8.0),       # same drop, later row
        _state("2026-11-13T13:37:36.321472+00:00", 7.8),
        SimpleNamespace(attributes = {}),                      # unavailable
    ]
    drops = cd.extract_drops(states, "Native")

    assert [(d["connessione"], d["durata_secondi"], d["periodica"]) for d in drops] == [
        ("Native", 8.0, True),      # on the 71.6 minute cadence
        ("Native", 7.8, False),     # off the cadence
    ]
    assert drops[0]["inizio"].startswith("2026-11-13T")


async def test_action_lists_recorded_drops_newest_first(recorder_mock, hass):
    registry = er.async_get(hass)
    native = registry.async_get_or_create(
        "binary_sensor", DOMAIN, "SERIAL_connectivity_native",
        suggested_object_id = "centrale_inim_connessione_native",
    ).entity_id
    primelan = registry.async_get_or_create(
        "binary_sensor", DOMAIN, "SERIAL_connectivity_primelan",
        suggested_object_id = "centrale_inim_connessione_primelan",
    ).entity_id
    other = registry.async_get_or_create("binary_sensor", DOMAIN, "SERIAL_fault_low_battery").entity_id

    from homeassistant.util import dt as dt_util
    now = dt_util.utcnow()
    first = (now.replace(microsecond = 0)).isoformat()

    hass.states.async_set(native, "on", {"ultimo_ok": "a", "ultima_caduta": None, "durata_ultima_caduta": None})
    hass.states.async_set(native, "on", {"ultimo_ok": "b", "ultima_caduta": first, "durata_ultima_caduta": 8.3})
    hass.states.async_set(native, "on", {"ultimo_ok": "c", "ultima_caduta": first, "durata_ultima_caduta": 8.3})
    hass.states.async_set(primelan, "on", {"ultimo_ok": "a", "ultima_caduta": first, "durata_ultima_caduta": 12.2})
    hass.states.async_set(other, "on", {"ultima_caduta": first, "durata_ultima_caduta": 1.0})
    await async_wait_recording_done(hass)

    cd.async_register(hass)
    answer = await hass.services.async_call(
        DOMAIN, cd.SERVICE_CONNECTION_DROPS, {}, blocking = True, return_response = True,
    )

    # the two connections of the panel dropped together: one drop
    assert answer["totale"] == 1
    assert answer["cadute"] == [{
        "inizio": answer["cadute"][0]["inizio"],
        "periodica": answer["cadute"][0]["periodica"],
        "native_secondi": 8.3,
        "primelan_secondi": 12.2,
    }]
    assert set(answer) == {"dal", "al", "totale", "periodiche", "altre", "cadute"}

    # the default is one day back
    from datetime import datetime, timedelta
    span = datetime.fromisoformat(answer["al"]) - datetime.fromisoformat(answer["dal"])
    assert span == timedelta(days = 1)
    answer = await hass.services.async_call(
        DOMAIN, cd.SERVICE_CONNECTION_DROPS, {"days": 3}, blocking = True, return_response = True,
    )
    assert answer["totale"] == 1
    span = datetime.fromisoformat(answer["al"]) - datetime.fromisoformat(answer["dal"])
    assert span == timedelta(days = 3)


def _raw(connection, at, duration, panel = "SERIAL"):
    from datetime import datetime, timedelta, timezone
    start = datetime(2026, 11, 17, 17, 4, 35, tzinfo = timezone.utc) + timedelta(seconds = at)
    return {
        "connessione": connection, "inizio": start.isoformat(), "durata_secondi": duration,
        "periodica": False, "_start": start, "_panel": panel,
    }


def test_drops_of_the_two_connections_close_together_are_one():
    both = {"SERIAL": {"Native", "PrimeLAN"}}
    grouped = cd.group_drops([
        _raw("PrimeLAN", 5, 11.6), _raw("Native", 0, 7.7),        # one drop of the panel
        _raw("Native", 3600, 9.3),                                  # Native only
    ], both)
    assert [{k: v for k, v in d.items() if k not in ("inizio",)} for d in grouped] == [
        {"periodica": False, "native_secondi": 9.3, "_only": "Native"},
        {"periodica": False, "native_secondi": 7.7, "primelan_secondi": 11.6},
    ]
    assert cd._count(grouped) == "2 (solo Native 1)"
    assert cd._count(grouped[1:]) == 1
    assert cd._count([]) == 0


def test_native_only_panel_is_not_marked_as_one_connection_only():
    grouped = cd.group_drops([_raw("Native", 0, 8.0), _raw("Native", 4000, 8.1)],
                             {"SERIAL": {"Native"}})
    assert cd._count(grouped) == 2


def test_drops_of_two_panels_are_not_merged():
    both = {"A": {"Native", "PrimeLAN"}, "B": {"Native", "PrimeLAN"}}
    grouped = cd.group_drops([_raw("Native", 0, 8.0, "A"), _raw("PrimeLAN", 2, 9.0, "B")], both)
    assert len(grouped) == 2
