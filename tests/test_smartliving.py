"""SmartLiving panels: setup, entities and commands over a simulated SmartLAN
board built from a real SmartLiving 1050 (configuration only, names replaced)."""
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.inim_prime.const import DOMAIN
from custom_components.inim_prime.smartliving.const import COMMANDS, MODELS
from custom_components.inim_prime.smartliving.protocol import Protocol, checksum

from . import smartliving_1050 as data

SERIAL = "1234567890123456"
MODEL = MODELS["1050"]


def _names(names):
    return b"".join(n.encode("ascii").ljust(16, b" ") for n in names)


class FakeSmartLiving:
    """Answers the SmartLAN requests the library sends; records the commands."""

    def __init__(self, pin: str = "123456"):
        self.pin = bytes(int(d) for d in pin).ljust(6, b"\xff")
        self.commands: list[tuple[int, bytes]] = []
        self.result = 0
        self.areas = bytearray(data.AREAS_STATUS)
        self.memory = {
            (0, 0x004000): data.FIRMWARE,
            (0, 0x00FEA0): SERIAL.encode(),
            (0, 0x002001): data.ZONES_STATUS,
            (0, 0x002002): data.ZONES_ENABLED,
            (0, 0x002003): data.ZONES_ALARM_MEMORY,
            (1, MODEL.area_names): _names(data.AREA_NAMES),
            (1, MODEL.zone_names): _names(data.ZONE_NAMES),
            (1, MODEL.scenario_names): _names(data.SCENARIO_NAMES),
            (0, 0x010000 + MODEL.terminal_table): data.TERMINAL_TABLE,
            (0, 0x010000 + MODEL.terminal_records): data.TERMINAL_RECORDS,
            (0, 0x010000 + MODEL.zone_records): data.ZONE_RECORDS,
            (0, 0x010000 + MODEL.scenario_records): data.SCENARIO_RECORDS,
            (0, 0x004014): bytes.fromhex("0000a000e006000a400be00b200d6013501430160017"),
            (1, 0x011700): _names(["Sirena int", "Uscita 1", "Uscita 2"]),
            (0, 0x001FFE): bytes.fromhex("0200 0000"),
            (1, 0x019204): (
                bytes.fromhex("10000000" "0100" "05" "02" "8a")
                + bytes.fromhex("20000000" "0100" "07" "00" "80")
            ),
        }

    def _lookup(self, bank, address, length):
        if (bank, address) == (0, 0x002000):
            return bytes(self.areas[:length])
        if (bank, address) == (0, 0x002004):
            return bytes([self.result])[:length].ljust(length, b"\x00")
        for (b, start), block in self.memory.items():
            if b == bank and start <= address and address + length <= start + len(block):
                return block[address - start:address - start + length]
        return bytes(length)

    async def exchange(self, protocol, request: bytes, answer_length: int) -> bytes:
        assert request[0] in (0x00, 0x01)
        assert checksum(request[:7]) == request[7]
        address = int.from_bytes(request[2:5], "big")
        if request[0] == 0x01:
            payload = request[8:]
            assert COMMANDS[address] == request[6] == len(payload)
            self.commands.append((address, payload))
            self.result = 0 if payload[:6] == self.pin else 1
            if address == 0x00200A and self.result == 0:
                record = data.SCENARIO_RECORDS[payload[6] * 8:payload[6] * 8 + 6]
                payload = payload[:6] + record + bytes(2)
                address = 0x002006
            if address == 0x002006 and self.result == 0:
                for i, byte in enumerate(payload[6:]):
                    for shift in (0, 4):
                        mode = byte >> shift & 0x0F
                        if mode:
                            self.areas[i] = (self.areas[i] & ~(0x0F << shift)) | (mode << shift)
            return bytes([checksum(payload)])
        answer = self._lookup(request[1], address, request[6])
        return answer + bytes([checksum(answer)])


def _patched(panel):
    async def _exchange(self, request, answer_length):
        return await panel.exchange(self, request, answer_length)
    return patch.object(Protocol, "_exchange", _exchange)


async def _setup(hass: HomeAssistant, panel: FakeSmartLiving, options: dict | None = None):
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, minor_version=0, unique_id=SERIAL,
        data={"serial_number": SERIAL, "host": "192.0.2.20",
              "smartliving": {"port": 5004, "password": "", "pin": "123456"}},
        options={"scan_intervals": {"zones": 60000, "partitions": 60000},
                 "hide_unused_partitions": True, **(options or {})},
    )
    entry.add_to_hass(hass)
    with _patched(panel):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_config_flow_creates_a_smartliving_entry(hass: HomeAssistant):
    panel = FakeSmartLiving()
    with _patched(panel), patch("custom_components.inim_prime.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] is FlowResultType.MENU
        # first the panel type: Prime (then how to connect) or SmartLiving
        assert result["menu_options"] == ["prime", "smartliving"]
        prime = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "prime"})
        assert prime["menu_options"] == ["native_only", "primelan_only", "both"]
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "smartliving"})
        assert result["step_id"] == "smartliving"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "192.0.2.20", "port": 5004, "pin": "123456"},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == SERIAL
    assert result["data"]["smartliving"] == {
        "port": 5004, "password": "", "pin": "123456", "port_check_server": "https://ifconfig.co",
    }
    assert not panel.commands


async def test_zones_areas_and_panel(hass: HomeAssistant):
    panel = FakeSmartLiving()
    entry = await _setup(hass, panel, {"alarm_panel_native": {"arm_away": {"total": [1, 2, 3, 4]}}})
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    zone_entities = [e for e in entities if "_zone_" in e.unique_id and e.domain == "binary_sensor"]
    # 22 terminals programmed, 2 of them outputs
    assert len({e.unique_id.split("_zone_")[1].split("_")[0] for e in zone_entities}) == 20
    panels = [e for e in entities if e.domain == "alarm_control_panel"]
    assert panels
    state = hass.states.get(panels[0].entity_id)
    assert state.state == "disarmed"
    assert not panel.commands

    with _patched(panel):
        await hass.services.async_call(
            "alarm_control_panel", "alarm_arm_away", {"entity_id": panels[0].entity_id}, blocking=True,
        )
        await hass.async_block_till_done()
    address, payload = panel.commands[0]
    assert address == 0x002006
    assert payload == bytes.fromhex("010203040506") + bytes.fromhex("1111000000000000")
    await hass.config_entries.async_unload(entry.entry_id)


async def test_zone_exclusion_and_disarm(hass: HomeAssistant):
    panel = FakeSmartLiving()
    entry = await _setup(hass, panel)
    registry = er.async_get(hass)
    switch = next(
        e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.domain == "switch" and "_zone_8" in e.unique_id
    )
    panel_entity = next(
        e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.domain == "alarm_control_panel"
    )
    with _patched(panel):
        await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
        await hass.services.async_call(
            "alarm_control_panel", "alarm_disarm", {"entity_id": panel_entity}, blocking=True,
        )
        await hass.async_block_till_done()
    pin = bytes.fromhex("010203040506")
    # zone 8 sits at physical index 11 on this panel
    assert (0x002009, pin + bytes.fromhex("0b0001")) in panel.commands
    disarm = [p for a, p in panel.commands if a == 0x002006]
    # the four areas in use (the others keep the panel default name)
    assert disarm and disarm[0] == pin + bytes.fromhex("4444000000000000")
    await hass.config_entries.async_unload(entry.entry_id)


async def test_diagnostics_hide_the_smartliving_serial(hass: HomeAssistant):
    from custom_components.inim_prime import diagnostics

    panel = FakeSmartLiving()
    entry = await _setup(hass, panel)
    result = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    assert result["panel"]["model"] == "1050"
    assert result["panel"]["firmware"] == "6.08"
    assert SERIAL not in str(result)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_scenarios_and_alarm_in_progress(hass: HomeAssistant):
    panel = FakeSmartLiving()
    entry = await _setup(hass, panel, {"alarm_panel": {"arm_night": 1}})
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    buttons = [e for e in entities if e.domain == "button" and "scenario" in e.unique_id]
    assert len(buttons) == 3  # only the programmed scenarios
    panel_entity = next(e.entity_id for e in entities if e.domain == "alarm_control_panel")

    # arm night = scenario 2 ("Notte"), id 1 on the wire
    with _patched(panel):
        await hass.services.async_call(
            "alarm_control_panel", "alarm_arm_night", {"entity_id": panel_entity}, blocking=True,
        )
        await hass.async_block_till_done()
    assert (0x00200A, bytes.fromhex("010203040506") + bytes([1])) in panel.commands

    # area 1 alarm bit (first bitmap after the 6 nibble bytes on a 1050)
    panel.areas[0:6] = bytes.fromhex("1111444444")+b"\x44"
    panel.areas[6] = 0x01
    coordinator = entry.runtime_data.coordinators.partitions
    entry.runtime_data.gateway.smartliving_adapter._refreshed_at = None  # skip the short reuse window
    with _patched(panel):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert hass.states.get(panel_entity).state == "triggered"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_outputs_and_event_log(hass: HomeAssistant):
    panel = FakeSmartLiving()
    entry = await _setup(hass, panel)
    gateway = entry.runtime_data.gateway
    assert sorted(gateway.outputs) == [5, 7, 51, 52, 53]
    registry = er.async_get(hass)
    switch = next(
        e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.domain == "switch" and e.unique_id.endswith("_output_7")
    )
    with _patched(panel):
        await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
        await hass.async_block_till_done()
        events = await gateway.get_log_events(10)
    assert (0x002007, bytes.fromhex("010203040506") + bytes([9, 1])) in panel.commands
    assert [e.type for e in events] == ["Allarme zona", "Inserimento"]
    assert events[0].location == "Zona prova 08"
    assert events[1].location == "Casa" and events[1].agent == "Utente 5"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_experimental_model_is_flagged_before_creating(hass: HomeAssistant):
    panel = FakeSmartLiving()
    panel.memory[(0, 0x004000)] = b"6.09 10100  "
    with _patched(panel), patch("custom_components.inim_prime.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "smartliving"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "192.0.2.21", "port": 5004, "pin": "123456"},
        )
        assert result["step_id"] == "smartliving_experimental"
        assert result["description_placeholders"]["model"] == "SmartLiving 10100"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert not panel.commands


async def test_smartlan_port_reachable_from_the_internet_stops_the_setup(hass: HomeAssistant):
    panel = FakeSmartLiving()

    async def exposed(_hass, _server, _port):
        return True

    with _patched(panel), patch("custom_components.inim_prime.flow.config_flow.async_port_reachable", exposed):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "smartliving"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "192.0.2.20", "port": 5004, "check_port_exposed": True},
        )
    assert result["errors"] == {"base": "smartliving_port_exposed"}
    assert result["description_placeholders"]["port"] == "5004"


async def test_options_offer_the_smartlan_port_check(hass: HomeAssistant):
    panel = FakeSmartLiving()
    entry = await _setup(hass, panel)

    async def closed(_hass, _server, _port):
        return False

    with _patched(panel), patch("custom_components.inim_prime.flow.options_flow.async_port_reachable", closed):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert "port_check_smartliving" in result["menu_options"]
        assert "port_check" not in result["menu_options"]
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "port_check_smartliving"})
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"port_check_server": "https://ifconfig.co"})
    assert "SmartLAN" in result["description_placeholders"]["result"]
    assert "5004" in result["description_placeholders"]["result"]
