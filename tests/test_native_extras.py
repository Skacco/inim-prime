"""Native scenarios, system faults and event log, on answers of a real Prime."""
import asyncio
from types import SimpleNamespace

from custom_components.inim_prime.adapters import native_adapter as na
from custom_components.inim_prime.adapters import native_extras as extras
from custom_components.inim_prime.gateway import InimPrimeGateway
from custom_components.inim_prime.models.system_faults import UnifiedSystemFault

from . import native_prime_data as d

LOG_ADDRESS = 0xA1D0
NEXT_INDEX = 0x0A2F


def _fake_client(active=d.SCENARIOS_ACTIVE_SPENTO, status=d.SYSTEM_STATUS_OK):
    records_start = LOG_ADDRESS + (NEXT_INDEX - len(d.RECORDS) // 14) * 14
    memory = {
        0x1A002400: d.LABEL_POINTERS,
        0x14030D20: d.AREA_LABELS,
        0x1403AFA0: d.KEYPAD_LABELS,
        0x1403D8E0: d.SCENARIO_LABELS,
        0x14038CA0: d.USER_LABELS,
        0x1A03786C: d.EVENT_TYPES,
        0x14595644: d.EVENT_TEXTS,
        records_start: d.RECORDS,
    }
    commands = []

    async def read_memory(address, length):
        for start, data in memory.items():
            if start <= address < start + len(data):
                chunk = data[address - start:address - start + length]
                return chunk + bytes(length - len(chunk))
        return bytes(length)

    async def exchange_payload(payload, operation, response_payload_length):
        op = int.from_bytes(payload[:4], "little")
        commands.append(payload)
        if op == 5:
            return d.SCENARIOS_PROGRAMMED if payload[-1] == 1 else active
        return {13: status, 18: d.LOG_INFO, 31: d.LOG_TABLES}.get(op, bytes(response_payload_length))

    client = SimpleNamespace(
        _lock = asyncio.Lock(),
        _protocol = SimpleNamespace(read_memory = read_memory, exchange_payload = exchange_payload),
        is_connected = True,
    )
    return client, commands


def test_scenario_masks():
    assert extras.parse_scenario_mask(d.SCENARIOS_PROGRAMMED) == frozenset(range(12))
    assert extras.parse_scenario_mask(d.SCENARIOS_ACTIVE_SPENTO) == {0}
    assert extras.parse_scenario_mask(d.SCENARIOS_ACTIVE_ACCESO_GIORNO) == {2}


def test_system_status():
    assert extras.parse_system_status(d.SYSTEM_STATUS_OK) == (0, 13.6)
    assert extras.parse_system_status(d.SYSTEM_STATUS_NO_PHONE_LINE)[0] == 0x10


def test_activation_payloads_match_the_app():
    # as captured, with the master PIN in place of the user code
    assert extras.scenario_prepare_payload(2) == bytes.fromhex("3c000000") + bytes(16) + b"\x02\x01" + b"\x74" + bytes(5)
    assert extras.scenario_activate_payload(2) == bytes.fromhex("3b0000000200") + b"\x74" + bytes(5)


async def test_scenarios_names_and_active():
    client, _ = _fake_client(active = d.SCENARIOS_ACTIVE_ACCESO_GIORNO)
    gateway = InimPrimeGateway(native = na.NativeAdapter(client))

    scenarios = await gateway.update_scenarios()

    assert len(scenarios) == 12
    assert scenarios[0].label == "Spento" and scenarios[2].label == "Acceso giorno"
    assert [s.scenario_id for s in scenarios.values() if s.active] == [2]


async def test_activate_scenario_sends_both_commands():
    client, commands = _fake_client()
    gateway = InimPrimeGateway(native = na.NativeAdapter(client))

    await gateway.activate_scenario(2)

    assert [int.from_bytes(c[:4], "little") for c in commands] == [60, 59]


async def test_system_faults():
    client, _ = _fake_client(status = d.SYSTEM_STATUS_NO_PHONE_LINE)
    gateway = InimPrimeGateway(native = na.NativeAdapter(client))

    faults = await gateway.update_system_faults()

    assert faults.faults == {UnifiedSystemFault.NO_TELEPHONE_LINE}
    assert faults.supply_voltage == 13.6


async def test_log_events_read_like_primelan():
    client, _ = _fake_client()
    gateway = InimPrimeGateway(native = na.NativeAdapter(client))

    events = await gateway.get_log_events(6)

    assert [(e.type, e.agent, e.location) for e in events] == [
        ("Disins. area     - Vol. Interrato", "Da remoto", "Home Assistant"),
        ("ScenarioAttivato", None, "Spento"),
        ("Fine Disins. area     - Vol. Interrato", "Da remoto", "Mario"),
        ("MancanzaLineaTel", None, None),
        ("Fine MancanzaLineaTel", None, None),
        ("Codice valido", "TAST. GARAGE", "Mario"),
    ]
    assert events[0].timestamp.isoformat() == "2026-11-26T13:07:23"
    assert events[-1].id == NEXT_INDEX - 1


def test_quick_arming_at_a_keypad_has_no_user():
    from types import SimpleNamespace as NS
    types = extras.parse_event_types(d.EVENT_TYPES, d.EVENT_TEXTS)
    event_type, _ = extras.find_event_type(types, 0x1C50)       # Area ins. parz.
    record = extras.parse_record(0, bytes.fromhex("cddd6a32080000c0501cff870200"))
    assert extras.record_user(event_type, record) == extras.NO_USER
    assert extras.record_source(event_type, record) == 2         # TAST. NOTTE, not remote


def test_a_log_read_in_another_form_does_not_replay_old_events():
    from datetime import datetime
    from custom_components.inim_prime.helpers.panel_log_events import filter_new_log_events
    from custom_components.inim_prime.models.log_events import UnifiedLogEvent as E

    def ev(minute, location):
        return E(1, datetime(2026, 11, 26, 15, minute), "t", None, location, None)

    last = [ev(0, None), ev(7, None)]                  # as PrimeLAN wrote them
    current = [ev(0, "Home Assistant"), ev(7, "Home Assistant"), ev(9, "Home Assistant")]
    assert [e.timestamp.minute for e in filter_new_log_events(last, current)] == [9]


async def test_old_primelan_defaults_move_to_the_native_ones(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.inim_prime import async_migrate_entry

    entry = MockConfigEntry(
        domain = "inim_prime", version = 2, minor_version = 0,
        data = {"serial_number": "1", "host": "h", "native": {"port": 6004}, "primelan": {"api_key": "k"}},
        options = {"scan_intervals": {
            "gsm": 60000.0, "system_faults": 60000.0, "panel_log_events": 30000.0, "scenarios": 10000.0,
        }},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.minor_version == 1
    assert entry.options["scan_intervals"] == {
        "gsm": 60000.0,                 # still PrimeLAN
        "system_faults": 10000,         # old default -> Native default
        "panel_log_events": 30000.0,    # chosen by the user: kept
        "scenarios": 2000,
    }


def test_scan_form_minimum_and_typos_in_seconds():
    from custom_components.inim_prime.flow.models import ConfigOptions
    from custom_components.inim_prime.flow.schemas import build_scan_intervals_schema

    options = ConfigOptions()
    options.system_faults_scan_interval = 60       # seconds typed as ms
    options.panel_log_events_scan_interval = 10000
    fields = build_scan_intervals_schema(
        has_native = True, has_primelan = True, current_options = options, as_section = False,
    )
    by_name = {str(k): (k.default(), v.config["min"]) for k, v in fields.items()}
    assert by_name["system_faults"] == (10000, 1000)
    assert by_name["panel_log_events"] == (10000, 1000)
    assert by_name["zones"][1] == 1
