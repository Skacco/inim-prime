"""Adapter that exposes inim.prime.primelan.InimPrimeClient through the unified interface.

PrimeLAN is currently the only backend that can report GSM status, system
faults, log events, and output dimming -- those methods here have no native
counterpart, which is exactly why InimPrimeGateway gates their availability
on whether a PrimelanAdapter is configured at all.
"""
from __future__ import annotations

from dataclasses import replace

import asyncio
import contextvars
import inspect
import logging
import math
import time
from collections import deque
from types import MappingProxyType

import aiohttp

from inim.prime.primelan.client import InimPrimeClient
from inim.prime.primelan.models.partition import (
    PartitionStatus as PrimelanPartitionStatus,
    ClearPartitionAlarmMemoryRequest,
    ArmingStatus as PrimelanArmingStatus,
    PartitionState as PrimelanPartitionState,
    SetPartitionArmingStatusRequest,
)
from inim.prime.primelan.models.system_faults import (
    SystemFault as PrimelanSystemFault,
    SystemFaultsStatus as PrimelanSystemFaultsStatus
)
from inim.prime.primelan.models.output import (
    OutputSetRequest,
    OutputStatus as PrimelanOutputStatus,
    OutputType as PrimelanOutputType,
)
from inim.prime.primelan.models.scenario import (
    ActivateScenarioRequest,
    ScenarioStatus as PrimelanScenarioStatus,
)
from inim.prime.primelan.models.zone import (
    ZoneState as PrimelanZoneState,
    ZoneStatus as PrimelanZoneStatus,
    ZoneBypassSetRequest,
)
from ..models.gsm import (
    UnifiedGSM,
)
from ..models.log_events import (
    UnifiedLogEvent,
)
from ..models.outputs import (
    UnifiedOutput,
)
from ..models.scenarios import (
    UnifiedScenario,
)
from ..models.partitions import (
    UnifiedPartitionState,
    UnifiedArmingStatus,
    UnifiedPartition,
)
from ..models.system_faults import (
    UnifiedSystemFaults,
    UnifiedSystemFault,
)
from ..models.zones import (
    UnifiedZoneState,
    UnifiedZone,
)

_ZONE_STATE_MAP: dict[PrimelanZoneState, UnifiedZoneState] = {
    PrimelanZoneState.TAMPER: UnifiedZoneState.TAMPER,
    PrimelanZoneState.STANDBY: UnifiedZoneState.STANDBY,
    PrimelanZoneState.ALARM: UnifiedZoneState.ALARM,
    PrimelanZoneState.SHORT_CIRCUIT: UnifiedZoneState.SHORT_CIRCUIT,
}

_PARTITION_STATE_MAP: dict[PrimelanPartitionState, UnifiedPartitionState] = {
    PrimelanPartitionState.OK: UnifiedPartitionState.OK,
    PrimelanPartitionState.ALARM: UnifiedPartitionState.ALARM,
    PrimelanPartitionState.TAMPER: UnifiedPartitionState.TAMPER,
}

_ARMING_STATUS_MAP: dict[PrimelanArmingStatus, UnifiedArmingStatus] = {
    PrimelanArmingStatus.ARM_AWAY: UnifiedArmingStatus.ARM_AWAY,
    PrimelanArmingStatus.ARM_STAY: UnifiedArmingStatus.ARM_STAY,
    PrimelanArmingStatus.ARM_INSTANT: UnifiedArmingStatus.ARM_INSTANT,
    PrimelanArmingStatus.DISARMED: UnifiedArmingStatus.DISARMED,
}
_UNIFIED_ARMING_STATUS_MAP: dict[UnifiedArmingStatus, PrimelanArmingStatus] = {v: k for k, v in _ARMING_STATUS_MAP.items()}

_SYSTEM_FAULT_MAP: dict[PrimelanSystemFault, UnifiedSystemFault] = {
    PrimelanSystemFault.RESERVED_0:                     UnifiedSystemFault.RESERVED_0,
    PrimelanSystemFault.RESERVED_1:                     UnifiedSystemFault.RESERVED_1,
    PrimelanSystemFault.LOW_BATTERY:                    UnifiedSystemFault.LOW_BATTERY,
    PrimelanSystemFault.NETWORK_FAULT:                  UnifiedSystemFault.NETWORK_FAULT,
    PrimelanSystemFault.NO_TELEPHONE_LINE:              UnifiedSystemFault.NO_TELEPHONE_LINE,
    PrimelanSystemFault.RADIO_JAMMING:                  UnifiedSystemFault.RADIO_JAMMING,
    PrimelanSystemFault.LOW_BATTERY_WIRELESS:           UnifiedSystemFault.LOW_BATTERY_WIRELESS,
    PrimelanSystemFault.WIRELESS_DEVICE_DISAPPEARANCE:  UnifiedSystemFault.WIRELESS_DEVICE_DISAPPEARANCE,
    PrimelanSystemFault.GSM_FAULT:                      UnifiedSystemFault.GSM_FAULT,
    PrimelanSystemFault.SENSOR_DIRTY:                   UnifiedSystemFault.SENSOR_DIRTY,
    PrimelanSystemFault.ZONE_FAULT:                     UnifiedSystemFault.ZONE_FAULT,
    PrimelanSystemFault.SIRENS_FAULT:                   UnifiedSystemFault.SIRENS_FAULT,
    PrimelanSystemFault.POWER_SUPPLY_FAULT:             UnifiedSystemFault.POWER_SUPPLY_FAULT,
    PrimelanSystemFault.RADIO_KEYBOARDS_FAULT:          UnifiedSystemFault.RADIO_KEYBOARDS_FAULT,
    PrimelanSystemFault.SABOTAGE_FAULT:                 UnifiedSystemFault.SABOTAGE_FAULT,
    PrimelanSystemFault.INTERNET_FAULT:                 UnifiedSystemFault.INTERNET_FAULT,
}

def _zone_to_unified(zone: PrimelanZoneStatus) -> UnifiedZone:
    unified_zone: UnifiedZone

    unified_zone = UnifiedZone(
        zone_id = zone.id,
        terminal_id = zone.terminal_id,
        label = zone.name,
        partitions = None,
        state = _ZONE_STATE_MAP[zone.state],
        bypass = zone.bypass,
        alarm_memory = zone.alarm_memory,
    )

    return unified_zone


def _partition_to_unified(partition: PrimelanPartitionStatus) -> UnifiedPartition:
    unified_partition: UnifiedPartition

    unified_partition = UnifiedPartition(
        partition_id = partition.id,
        label = partition.name,
        zones = None,
        arming_status = _ARMING_STATUS_MAP[partition.arming_status],
        partition_state = _PARTITION_STATE_MAP[partition.state],
        alarm_memory = partition.alarm_memory,
    )

    return unified_partition

def _output_to_unified(output: PrimelanOutputStatus) -> UnifiedOutput:
    dimmable = output.type == PrimelanOutputType.DIMMING
    return UnifiedOutput(
        output_id = output.id,
        label = (output.name or "").strip(),
        state = output.state > 0,
        level = max(0, min(100, output.state)) if dimmable else None,
        dimmable = dimmable,
    )


def _scenario_to_unified(scenario: PrimelanScenarioStatus) -> UnifiedScenario:
    return UnifiedScenario(
        scenario_id = scenario.id,
        label = (scenario.name or "").strip(),
        active = scenario.state,
    )


from ..const import (
    NATIVE_CYCLE_PAUSE_LEAD_FACTOR,
    NATIVE_CYCLE_PAUSE_LEAD_MARGIN,
    NATIVE_CYCLE_PAUSE_LEAD_MAX,
    NATIVE_CYCLE_PAUSE_LEAD_MIN,
    NATIVE_CYCLE_PAUSE_LEAD_PERCENTILE,
    PRIMELAN_COMMAND_RETRY_INTERVAL,
    PRIMELAN_COMMAND_RETRY_WINDOW,
    PRIMELAN_REQUEST_DURATIONS_KEPT,
)

_LOGGER = logging.getLogger(__name__)

# Indirection so tests can skip the waits.
_sleep = asyncio.sleep

# Set while the adapter itself reopens the client after a pause: its own
# check request must not wait for the pause to end.
_REOPENING: contextvars.ContextVar[bool] = contextvars.ContextVar("primelan_reopening", default = False)


class PrimelanAdapter:
    """Wraps a connected inim.prime.primelan.InimPrimeClient instance."""

    __slots__ = (
        "_client",
        "_zones",
        "_partitions",
        "_outputs",
        "_output_terminal_labels",
        "_scenarios",
        "_gsm",
        "_system_faults",
        "_held_commands",
        "_open",
        "_in_flight",
        "_idle",
        "_paused",
        "_request_durations",
    )

    def __init__(self, client: InimPrimeClient) -> None:
        self._client = client

        self._zones: dict[int, UnifiedZone] = {}
        self._partitions: dict[int, UnifiedPartition] = {}
        self._outputs: dict[int, UnifiedOutput] = {}
        # Physical terminal id -> label, used to match PrimeLAN outputs against
        # native ones (native keys outputs by terminal id, PrimeLAN by its own
        # output id, so the two only line up on the terminal).
        self._output_terminal_labels: dict[int, str] = {}
        self._scenarios: dict[int, UnifiedScenario] = {}
        self._gsm: UnifiedGSM | None = None
        self._system_faults: UnifiedSystemFaults | None = None
        # Commands currently waiting for the panel to accept connections again.
        self._held_commands: set = set()
        # Cycle pause: while _open is clear no new request is sent (they wait),
        # and the client's connections are closed once the last one has ended.
        self._open = asyncio.Event()
        self._open.set()
        self._in_flight = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._paused = False
        # (command, seconds) of the last requests, for the pause's lead
        self._request_durations: deque[tuple[str, float]] = deque(maxlen = PRIMELAN_REQUEST_DURATIONS_KEPT)
        request = getattr(client, "_request", None)

        async def _request(*args, **kwargs):
            if not _REOPENING.get():
                await self._open.wait()
            self._in_flight += 1
            self._idle.clear()
            started = time.monotonic()
            try:
                # looked up at every call (not the one bound at start), so a
                # replacement of the client's method is still honoured
                original = getattr(type(client), "_request")
                if inspect.isfunction(original):
                    return await original(client, *args, **kwargs)
                return await original(*args, **kwargs)
            finally:
                kind = str(kwargs.get("cmd", args[0] if args else "other"))
                self._request_durations.append((kind, time.monotonic() - started))
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._idle.set()

        # every PrimeLAN request goes through the client's _request
        if inspect.iscoroutinefunction(request):
            client._request = _request

    async def _send_command(self, coro_factory, key):
        """Send a command, retrying while the panel refuses the connection.

        During its periodic drop the panel resets connections for a few
        seconds. A command whose connection could not be opened at all
        (aiohttp.ClientConnectorError) never reached the panel, so it is safe
        to send it again; it is retried for PRIMELAN_COMMAND_RETRY_WINDOW.
        Any other failure is reported as before: the panel may have executed
        the command, and repeating e.g. an output pulse would undo it.

        The same command issued again while the first is still waiting is
        dropped (a second tap must not become a second pulse).
        """
        if key in self._held_commands:
            _LOGGER.debug(
                "Command %s ignored: the same command is already waiting for the panel", key,
            )
            return None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + PRIMELAN_COMMAND_RETRY_WINDOW
        held = False
        try:
            while True:
                try:
                    result = await coro_factory()
                except aiohttp.ClientConnectorError as err:
                    if loop.time() >= deadline:
                        raise
                    if not held:
                        held = True
                        self._held_commands.add(key)
                        _LOGGER.debug(
                            "Command %s held: the panel is not accepting connections (%s), retrying",
                            key, err,
                        )
                    await _sleep(PRIMELAN_COMMAND_RETRY_INTERVAL)
                    continue
                if held:
                    _LOGGER.debug("Command %s sent: the panel is back", key)
                return result
        finally:
            self._held_commands.discard(key)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        await self._client.connect()

    async def close(self) -> None:
        await self._client.close()

    # ------------------------------------------------------------------
    # Cycle pause
    # ------------------------------------------------------------------
    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def longest_request_by_kind(self) -> dict[str, float]:
        """Longest recently measured request of each command, slowest first."""
        longest: dict[str, float] = {}
        for kind, duration in self._request_durations:
            if duration > longest.get(kind, 0.0):
                longest[kind] = duration
        return dict(sorted(longest.items(), key = lambda item: -item[1]))

    @property
    def typical_longest_request(self) -> float | None:
        """Duration that NATIVE_CYCLE_PAUSE_LEAD_PERCENTILE of the recently
        measured requests do not exceed (seconds), if any."""
        if not self._request_durations:
            return None
        durations = sorted(duration for _, duration in self._request_durations)
        index = max(math.ceil(len(durations) * NATIVE_CYCLE_PAUSE_LEAD_PERCENTILE) - 1, 0)
        return durations[index]

    @property
    def request_lead(self) -> float:
        """How long before the planned close no new request should be sent:
        measured on this panel like Native's. Until a request has been
        measured, the longest lead allowed."""
        typical = self.typical_longest_request
        if typical is None:
            return NATIVE_CYCLE_PAUSE_LEAD_MAX
        lead = typical * NATIVE_CYCLE_PAUSE_LEAD_FACTOR + NATIVE_CYCLE_PAUSE_LEAD_MARGIN
        return min(max(lead, NATIVE_CYCLE_PAUSE_LEAD_MIN), NATIVE_CYCLE_PAUSE_LEAD_MAX)

    async def pause(self, close_at: float, latest: float) -> tuple[float, int]:
        """Stop sending requests now and close the client's connections at
        close_at (wall-clock time), or as soon as the requests in flight have
        ended if that is later, but not after latest: a request still running
        then is cut. Until resume() the requests wait.

        Returns the wall-clock time the connections were closed at and how
        many requests were still in flight at close_at."""
        self._paused = True
        self._open.clear()
        while (remaining := close_at - time.time()) > 0:
            await asyncio.sleep(min(remaining, 0.5))
        in_flight = self._in_flight
        try:
            await asyncio.wait_for(self._idle.wait(), max(latest - time.time(), 0.0))
        except TimeoutError:
            pass
        # also the idle kept-alive connections: none may be open at the instant
        await self._client.close()
        return time.time(), in_flight

    def release(self) -> None:
        """End the pause without reopening (the integration is unloading)."""
        self._paused = False
        self._open.set()

    async def resume(self) -> bool:
        """Reopen the client and let the requests go on. False when the panel
        did not answer the check request (the requests then retry as usual)."""
        if not self._paused:
            return True
        token = _REOPENING.set(True)
        try:
            await self._client.connect()
            return True
        except Exception as err:  # noqa: BLE001 - the panel may be restarting
            _LOGGER.debug("PrimeLAN check after the pause failed (%s)", err)
            return False
        finally:
            _REOPENING.reset(token)
            self._paused = False
            self._open.set()

    # ------------------------------------------------------------------
    #   Properties
    # ------------------------------------------------------------------
    @property
    def zones(self) -> MappingProxyType[int, UnifiedZone]:
        return MappingProxyType[int, UnifiedZone](self._zones)

    @property
    def partitions(self) -> MappingProxyType[int, UnifiedPartition]:
        return MappingProxyType[int, UnifiedPartition](self._partitions)

    @property
    def outputs(self) -> MappingProxyType[int, UnifiedOutput]:
        return MappingProxyType[int, UnifiedOutput](self._outputs)

    @property
    def output_terminal_labels(self) -> MappingProxyType[int, str]:
        """Terminal id -> label for every output PrimeLAN publishes.

        Keyed by the physical terminal so it can be matched against native
        outputs (which are keyed by terminal id, not by PrimeLAN output id).
        """
        return MappingProxyType[int, str](self._output_terminal_labels)

    @property
    def scenarios(self) -> MappingProxyType[int, UnifiedScenario]:
        return MappingProxyType[int, UnifiedScenario](self._scenarios)

    @property
    def gsm(self) -> UnifiedGSM | None:
        return self._gsm

    @property
    def system_faults(self) -> UnifiedSystemFaults | None:
        return self._system_faults

    @property
    def count_bypassed_zones(self) -> int:
        return sum(1 for zone in self._zones.values() if zone.bypass == True)

    @property
    def count_zone_alarm_memories(self) -> int:
        return sum(1 for zone in self._zones.values() if zone.alarm_memory == True)

    @property
    def count_partition_alarm_memories(self) -> int:
        return sum(1 for partition in self._partitions.values() if partition.alarm_memory == True)

    def get_zone(self, zone_id: int) -> UnifiedZone | None:
        return self._zones.get(zone_id)

    def get_partition(self, partition_id: int) -> UnifiedPartition | None:
        return self._partitions.get(partition_id)

    # State reported by a panel event (webhook), applied to the cache until the
    # next read replaces it. False when the object is not known.
    def apply_zone_state(self, zone_id: int, state: UnifiedZoneState) -> bool:
        zone = self._zones.get(zone_id)
        if zone is None:
            return False
        self._zones = {**self._zones, zone_id: replace(zone, state = state)}
        return True

    def apply_partition_changes(self, partition_id: int, **changes) -> bool:
        partition = self._partitions.get(partition_id)
        if partition is None:
            return False
        self._partitions = {**self._partitions, partition_id: replace(partition, **changes)}
        return True

    def apply_output_state(self, output_id: int, state: bool) -> bool:
        output = self._outputs.get(output_id)
        if output is None:
            return False
        self._outputs = {**self._outputs, output_id: replace(output, state = state)}
        return True

    # ------------------------------------------------------------------
    # Updaters
    # ------------------------------------------------------------------
    async def update_zones(self) -> MappingProxyType[int, UnifiedZone]:
        primelan_zones = await self._client.get_zones_status()

        zones: dict[int, UnifiedZone] = {}
        for zone_id, zone in primelan_zones.items():
            zones[zone_id] = _zone_to_unified(zone)
        self._zones = zones
        return self.zones

    async def update_partitions(self) -> MappingProxyType[int, UnifiedPartition]:
        primelan_partitions = await self._client.get_partitions_status()

        partitions: dict[int, UnifiedPartition] = {}
        for partition_id, partition in primelan_partitions.items():
            partitions[partition_id] = _partition_to_unified(partition)
        self._partitions = partitions
        return self.partitions

    async def update_outputs(self) -> MappingProxyType[int, UnifiedOutput]:
        primelan_outputs = await self._client.get_outputs_status()
        self._outputs = {
            output_id: _output_to_unified(output)
            for output_id, output in primelan_outputs.items()
        }
        self._output_terminal_labels = {
            output.terminal: (output.name or "").strip()
            for output in primelan_outputs.values()
        }
        return self.outputs

    async def update_scenarios(self) -> MappingProxyType[int, UnifiedScenario]:
        primelan_scenarios = await self._client.get_scenarios_status()
        self._scenarios = {
            scenario_id: _scenario_to_unified(scenario)
            for scenario_id, scenario in primelan_scenarios.items()
        }
        return self.scenarios

    async def update_gsm(self) -> UnifiedGSM:
        primelan_gsm = await self._client.get_gsm_status()
        gsm = UnifiedGSM(
            supply_voltage = primelan_gsm.supply_voltage,
            firmware_version = primelan_gsm.firmware_version,
            operator = primelan_gsm.operator,
            signal_strength = primelan_gsm.signal_strength,
            credit = primelan_gsm.credit,
        )
        self._gsm = gsm
        return gsm

    async def update_system_faults(self) -> UnifiedSystemFaults:
        primelan_system_faults: PrimelanSystemFaultsStatus = await self._client.get_system_faults_status()
        faults: frozenset[PrimelanSystemFault] = primelan_system_faults.faults

        system_faults = UnifiedSystemFaults(
            supply_voltage = primelan_system_faults.supply_voltage,
            faults = frozenset[UnifiedSystemFault](
                _SYSTEM_FAULT_MAP[f] for f in faults if f in _SYSTEM_FAULT_MAP
            ),
        )
        self._system_faults = system_faults
        return system_faults

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    ### Zones
    async def set_zone_bypass(
            self,
            zone_id: int,
            bypass: bool
    ) -> None:
        if zone_id not in self._zones:
            raise KeyError(f"Unknown zone id: {zone_id}")

        request = ZoneBypassSetRequest(zone_id = zone_id, bypass = bypass)
        await self._send_command(
            lambda: self._client.set_zone_bypass(request), ("zone_bypass", zone_id, bypass),
        )

    async def set_all_zone_bypasses(
            self,
            bypass: bool,
    ) -> None:
        """Set all zones bypass value. The native gateway changes only those which aren't currently in that state."""
        for zone in self._zones.values():
            if zone.bypass is not None and zone.bypass == bypass:
                continue
            await self.set_zone_bypass(zone.zone_id, bypass)


    ### Partition arming status
    async def set_partition_arming_status(
            self,
            partition_id: int,
            arming_status: UnifiedArmingStatus
    ) -> None:
        if partition_id not in self._partitions:
            raise KeyError(f"Unknown partition id: {partition_id}")

        request = SetPartitionArmingStatusRequest(
            partition_id = partition_id,
            arming_status = _UNIFIED_ARMING_STATUS_MAP[arming_status],
        )
        await self._send_command(
            lambda: self._client.set_partition_mode(request),
            ("arming_status", partition_id, arming_status),
        )


    async def set_partition_arming_statuses(
            self,
            arming_statuses: dict[int, UnifiedArmingStatus],
    ) -> None:
        for partition_id, arming_status in arming_statuses.items():
            await self.set_partition_arming_status(partition_id, arming_status)


    async def set_all_partitions_arming_status(
            self,
            arming_status: UnifiedArmingStatus,
    ) -> None:
        for partition_id in self._partitions.keys():
            await self.set_partition_arming_status(partition_id, arming_status)


    ### Reset partition memory
    async def reset_partition_memory(
            self,
            partition_id: int,
    ) -> None:
        if partition_id not in self._partitions:
            raise KeyError(f"Unknown partition id: {partition_id}")

        request = ClearPartitionAlarmMemoryRequest(
            partition_id = partition_id,
        )
        await self._send_command(
            lambda: self._client.clear_partition_alarm_memory(request),
            ("reset_memory", partition_id),
        )

    async def reset_partition_memories(
            self,
            partition_ids: set[int],
    ) -> None:
        for partition_id in partition_ids:
            partition = self._partitions.get(partition_id)
            if partition is not None:
                if partition.alarm_memory is not None and partition.alarm_memory == False:
                    continue
                await self.reset_partition_memory(partition_id)


    async def reset_all_partition_memories(self) -> None:
        for partition in self._partitions.values():
            if partition.alarm_memory is not None and partition.alarm_memory == False:
                continue
            await self.reset_partition_memory(partition.partition_id)



    ### Outputs
    async def set_output(
            self,
            output_id: int,
            state: bool,
            level: int | None = None,
    ) -> None:
        """Switch an output. For dimmable outputs `level` (1..100) sets the
        brightness; turning a dimmer on without a level restores the last
        known level, or 100 if unknown."""
        if output_id not in self._outputs:
            raise KeyError(f"Unknown output id: {output_id}")

        output = self._outputs[output_id]

        if not state:
            value = 0
        elif output.dimmable:
            if level is None:
                level = output.level if output.level else 100
            value = max(1, min(100, int(level)))
        else:
            value = 1

        request = OutputSetRequest(output_id = output_id, value = value)
        await self._send_command(
            lambda: self._client.set_output(request), ("output", output_id, value),
        )

    ### Scenarios
    async def activate_scenario(self, scenario_id: int) -> None:
        if scenario_id not in self._scenarios:
            raise KeyError(f"Unknown scenario id: {scenario_id}")

        request = ActivateScenarioRequest(scenario_id = scenario_id)
        await self._send_command(
            lambda: self._client.activate_scenario(request), ("scenario", scenario_id),
        )

    # ------------------------------------------------------------------
    # PrimeLAN-exclusive features
    # ------------------------------------------------------------------


    async def get_log_events(self, limit: int) -> list[UnifiedLogEvent]:
        events = await self._client.get_log_events(limit = limit)
        return [
            UnifiedLogEvent(
                id = event.id,
                timestamp = event.timestamp,
                type = event.type,
                agent = event.agent,
                location = event.location,
                value = event.value,
            )
            for event in events
        ]
