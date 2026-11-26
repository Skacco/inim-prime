"""High-level read-only client for a SmartLiving panel."""
from __future__ import annotations

from .const import (
    ARM_ADDRESS,
    AREAS_STATUS_ADDRESS,
    COMMAND_RESULT_BAD_PIN,
    COMMAND_RESULT_OK,
    EXCLUDE_ADDRESS,
    RESET_MEMORY_ADDRESS,
    SCENARIO_ADDRESS,
    BANK_MAIN,
    BANK_NAMES,
    CONFIG_BASE,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    FIRMWARE_ADDRESS,
    FIRMWARE_LENGTH,
    SERIAL_ADDRESS,
    SERIAL_LENGTH,
    NAME_LENGTH,
    TERMINAL_RECORD_SIZE,
    ZONE_RECORD_SIZE,
    ZONES_ALARM_MEMORY_ADDRESS,
    ZONES_ALARM_MEMORY_LENGTH,
    ZONES_ENABLED_ADDRESS,
    ZONES_ENABLED_LENGTH,
    ZONES_STATUS_ADDRESS,
    ZONES_STATUS_LENGTH,
    Model,
    model_from_firmware,
)
from .models import Configuration, Output, PanelInfo, Status, Zone
from .parsing import (
    is_output_type,
    active_scenarios,
    area_status_length,
    parse_area_status,
    parse_scenarios,
    parse_names,
    parse_panel_info,
    parse_serial,
    parse_terminal_table,
    parse_terminal_types,
    parse_zone_areas,
    parse_zone_status,
)
from .commands import (
    ArmMode,
    arm_payload,
    exclude_payload,
    reset_memory_payload,
    scenario_payload,
)
from .protocol import Protocol, SmartLivingCommandError, SmartLivingError


class UnsupportedModelError(SmartLivingError):
    """The panel reported a model this library has no addresses for."""


class InvalidPinError(SmartLivingCommandError):
    """The panel refused the PIN for this command."""


class Client:
    """Reads and commands a SmartLiving panel through its SmartLAN board.

    Call ``read_configuration()`` once, then ``read_status()`` as often as
    needed. Commands (arm, disarm, exclude, scenarios, memory reset) take the
    user PIN and are never retried.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        password: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._protocol = Protocol(host, port, password, timeout)
        self._model: Model | None = None
        self._configuration: Configuration | None = None

    @property
    def model(self) -> Model | None:
        return self._model

    @property
    def configuration(self) -> Configuration | None:
        return self._configuration

    async def close(self) -> None:
        await self._protocol.close()

    async def read_panel_info(self) -> PanelInfo:
        raw = await self._protocol.read(BANK_MAIN, FIRMWARE_ADDRESS, FIRMWARE_LENGTH)
        info = parse_panel_info(raw)
        serial = parse_serial(await self._protocol.read(BANK_MAIN, SERIAL_ADDRESS, SERIAL_LENGTH))
        info = PanelInfo(firmware=info.firmware, model=info.model, raw=info.raw, serial=serial)
        self._model = model_from_firmware(info.raw)
        return info

    async def _names(self, address: int, count: int) -> dict[int, str]:
        raw = await self._protocol.read(BANK_NAMES, address, count * NAME_LENGTH)
        return parse_names(raw, count)

    async def _config(self, offset: int, length: int) -> bytes:
        return await self._protocol.read(BANK_MAIN, CONFIG_BASE + offset, length)

    async def read_configuration(self) -> Configuration:
        """Model, names, zones (with their areas) and outputs."""
        info = await self.read_panel_info()
        model = self._model
        if model is None:
            raise UnsupportedModelError(f"unsupported panel {info.raw!r}")

        area_names = await self._names(model.area_names, model.areas)
        zone_names = await self._names(model.zone_names, model.zones)
        scenario_names = await self._names(model.scenario_names, model.scenarios)
        scenarios = parse_scenarios(
            await self._config(model.scenario_records, model.scenarios * model.scenario_record_size),
            model.scenario_record_size,
            scenario_names,
            model.areas,
        )

        table = parse_terminal_table(await self._config(model.terminal_table, model.physical_terminals))
        types = parse_terminal_types(
            await self._config(model.terminal_records, model.zones * TERMINAL_RECORD_SIZE), model.zones
        )
        zone_areas: dict[int, frozenset[int]] = {}
        if model.zone_records is not None:
            zone_areas = parse_zone_areas(
                await self._config(model.zone_records, model.zones * ZONE_RECORD_SIZE),
                model.zones,
                model.areas,
            )

        zones: dict[int, Zone] = {}
        outputs: dict[int, Output] = {}
        for terminal, physical_index in sorted(table.items()):
            if terminal > model.zones:
                continue
            name = zone_names.get(terminal, "")
            if is_output_type(types.get(terminal, 0)):
                outputs[terminal] = Output(terminal, name, physical_index)
            else:
                zones[terminal] = Zone(terminal, name, physical_index, zone_areas.get(terminal, frozenset()))

        self._configuration = Configuration(
            info=info,
            area_names=area_names,
            scenario_names=scenario_names,
            zones=zones,
            outputs=outputs,
            scenarios=scenarios,
        )
        return self._configuration

    async def read_status(self) -> Status:
        """Live state of areas and of the configured zones."""
        if self._configuration is None or self._model is None:
            await self.read_configuration()
        assert self._configuration is not None and self._model is not None
        model = self._model
        # Same lengths on every model (they cover 100 zones).
        areas_raw = await self._protocol.read(BANK_MAIN, AREAS_STATUS_ADDRESS, area_status_length(model.areas))
        states = await self._protocol.read(BANK_MAIN, ZONES_STATUS_ADDRESS, ZONES_STATUS_LENGTH)
        enabled = await self._protocol.read(BANK_MAIN, ZONES_ENABLED_ADDRESS, ZONES_ENABLED_LENGTH)
        memory = await self._protocol.read(BANK_MAIN, ZONES_ALARM_MEMORY_ADDRESS, ZONES_ALARM_MEMORY_LENGTH)
        areas = parse_area_status(areas_raw, model.areas)
        return Status(
            areas=areas,
            zones=parse_zone_status(states, enabled, memory, list(self._configuration.zones)),
            active_scenarios=active_scenarios(self._configuration.scenarios, areas),
        )

    async def _command(self, address: int, payload: bytes) -> None:
        result = await self._protocol.command(address, payload)
        if result == COMMAND_RESULT_BAD_PIN:
            raise InvalidPinError("PIN refused by the panel")
        if result != COMMAND_RESULT_OK:
            raise SmartLivingCommandError(f"command refused by the panel (result {result:#04x})")

    async def set_areas(self, pin: str, modes: dict[int, ArmMode]) -> None:
        """Arm or disarm areas, e.g. {1: ArmMode.AWAY, 2: ArmMode.DISARM}."""
        await self._command(ARM_ADDRESS, arm_payload(pin, modes))

    async def reset_alarm_memory(self, pin: str, areas: set[int]) -> None:
        await self._command(RESET_MEMORY_ADDRESS, reset_memory_payload(pin, areas))

    async def set_zone_excluded(self, pin: str, zone_id: int, excluded: bool) -> None:
        if self._configuration is None:
            await self.read_configuration()
        assert self._configuration is not None
        zone = self._configuration.zones.get(zone_id)
        if zone is None:
            raise ValueError(f"zone {zone_id} is not programmed")
        await self._command(EXCLUDE_ADDRESS, exclude_payload(pin, zone.physical_index, excluded))

    async def activate_scenario(self, pin: str, scenario: int) -> None:
        """Scenario 1-30."""
        await self._command(SCENARIO_ADDRESS, scenario_payload(pin, scenario))
