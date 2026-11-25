"""The diagnostics download must work and be JSON-serialisable."""
from types import SimpleNamespace

from homeassistant.helpers.json import json_bytes

from custom_components.inim_prime import diagnostics
from custom_components.inim_prime.models.zones import UnifiedZone, UnifiedZoneState


async def test_entry_diagnostics_lists_zones_and_partitions(monkeypatch):
    zone = UnifiedZone(
        zone_id = 3, terminal_id = 2, label = "Porta", partitions = frozenset({1}),
        state = UnifiedZoneState.STANDBY, bypass = False, alarm_memory = False,
        native_status_bytes = b"\x01\x02",
    )
    partition = SimpleNamespace(
        partition_id = 1, label = "Casa", partition_state = None,
        arming_status = None, alarm_memory = False,
    )
    gateway = SimpleNamespace(
        backends_active = ["native"], zones = {3: zone}, partitions = {1: partition},
        system_faults = None, gsm = None,
        scenarios = {2: SimpleNamespace(label = "Totale", active = True)},
        native_adapter = SimpleNamespace(panel_firmware_and_model = ("2.01", "PRIME060")),
    )
    entry = SimpleNamespace(
        runtime_data = SimpleNamespace(gateway = gateway),
        options = {"alarm_pin": "1234", "alarm_panel_native": {"arm_away": {"total": [0, 1]}}},
    )
    monkeypatch.setattr(diagnostics, "get_entry_data", lambda _entry: {"serial_number": "x"})

    result = await diagnostics.async_get_config_entry_diagnostics(None, entry)

    assert result["panel"]["firmware"] == "2.01"
    assert result["panel"]["model"] == "PRIME060"
    assert result["zones"][3]["native_status_bytes"] == "01 02"
    assert result["partitions"][1]["label"] == "Casa"
    assert result["options"]["alarm_pin"] == "**REDACTED**"
    assert result["options"]["alarm_panel_native"] == {"arm_away": {"total": [0, 1]}}
    assert result["scenarios"][2] == {"label": "Totale", "active": True}
    json_bytes(result)
