"""The diagnostics download must work, be JSON-serialisable and leave secrets out."""
from datetime import datetime
from types import SimpleNamespace

from homeassistant.helpers.json import json_bytes

from custom_components.inim_prime import diagnostics
from custom_components.inim_prime.const import DOMAIN
from custom_components.inim_prime.models.log_events import UnifiedLogEvent
from custom_components.inim_prime.models.zones import UnifiedZone, UnifiedZoneState

REDACTED = "**REDACTED**"


def _gateway():
    zone = UnifiedZone(
        zone_id = 3, terminal_id = 2, label = "Porta", partitions = frozenset({1}),
        state = UnifiedZoneState.STANDBY, bypass = False, alarm_memory = False,
        native_status_bytes = b"\x01\x02",
    )
    partition = SimpleNamespace(
        partition_id = 1, label = "Casa", partition_state = None,
        arming_status = None, alarm_memory = False,
    )
    return SimpleNamespace(
        backends_active = ["native"], zones = {3: zone}, partitions = {1: partition},
        system_faults = None, gsm = None,
        scenarios = {2: SimpleNamespace(label = "Totale", active = True)},
        native_adapter = SimpleNamespace(panel_firmware_and_model = ("2.01", "PRIME060")),
    )


async def test_entry_diagnostics_lists_zones_and_partitions(monkeypatch):
    entry = SimpleNamespace(
        runtime_data = SimpleNamespace(gateway = _gateway()),
        options = {
            "alarm_pin": "1234",
            "webhook_id": "secret-webhook",
            "alarm_panel_native": {"arm_away": {"total": [0, 1]}},
        },
    )
    monkeypatch.setattr(diagnostics, "get_entry_data", lambda _entry: {"serial_number": "123456"})

    result = await diagnostics.async_get_config_entry_diagnostics(None, entry)

    assert result["panel"]["serial_number"] == REDACTED
    assert result["panel"]["firmware"] == "2.01"
    assert result["panel"]["model"] == "PRIME060"
    assert result["zones"][3]["native_status_bytes"] == "01 02"
    assert result["partitions"][1]["label"] == "Casa"
    assert result["options"]["alarm_pin"] == REDACTED
    assert result["options"]["webhook_id"] == REDACTED
    assert result["options"]["alarm_panel_native"] == {"arm_away": {"total": [0, 1]}}
    assert result["scenarios"][2] == {"label": "Totale", "active": True}
    assert "123456" not in json_bytes(result).decode()


async def test_panel_device_diagnostics_redacts_serial_and_agent(monkeypatch):
    event = UnifiedLogEvent(
        id = 7, timestamp = datetime(2026, 11, 26, 7, 0), type = "Disinserimento",
        agent = "Mario Rossi", location = "Tastiera 1", value = None,
    )
    entry = SimpleNamespace(
        runtime_data = SimpleNamespace(
            gateway = _gateway(),
            coordinators = SimpleNamespace(
                panel_log_events = SimpleNamespace(last_panel_log_events = [event]),
            ),
        ),
    )
    device = SimpleNamespace(identifiers = {(DOMAIN, "123456")})
    monkeypatch.setattr(diagnostics, "get_entry_data", lambda _entry: {"serial_number": "123456"})

    result = await diagnostics.async_get_device_diagnostics(None, entry, device)

    assert result["serial_number"] == REDACTED
    assert result["log_events"][0]["agent"] == REDACTED
    assert result["log_events"][0]["location"] == REDACTED
    assert result["log_events"][0]["type"] == "Disinserimento"
    text = json_bytes(result).decode()
    assert "Mario Rossi" not in text
    assert "123456" not in text


def test_log_events_hide_the_user_but_keep_zone_and_scenario_names():
    def event(agent, location):
        return UnifiedLogEvent(
            id = 1, timestamp = datetime(2026, 11, 26), type = "t",
            agent = agent, location = location, value = None,
        )

    result = diagnostics._log_events([
        event("Da remoto", "Mario"),            # area disarmed by a user
        event("TAST. GARAGE", "Mario"),         # valid code at a keypad
        event(None, "Spento"),                  # scenario activated
    ])

    assert [(e["agent"], e["location"]) for e in result] == [
        (REDACTED, REDACTED), (REDACTED, REDACTED), (None, "Spento"),
    ]
