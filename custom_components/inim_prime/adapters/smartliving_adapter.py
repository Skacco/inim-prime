"""SmartLiving panels (SmartLAN board) behind the same API the gateway uses
for the Prime native backend: zones, areas, arming, exclusion, memory reset.
"""
from __future__ import annotations

import logging
import time
from dataclasses import replace
from types import MappingProxyType

from ..models.partitions import UnifiedArmingStatus, UnifiedPartition, UnifiedPartitionState
from ..models.zones import UnifiedZone, UnifiedZoneState
from ..smartliving import (
    AreaState,
    ArmMode,
    Client,
    Configuration,
    SmartLivingConnectionError,
    SmartLivingError,
    ZoneState,
)

_LOGGER = logging.getLogger(__name__)

# Zones and areas come from the same four reads: when both coordinators poll
# back to back, the second one reuses the first read.
_STATUS_REUSE = 0.3

_AREA_STATE_TO_UNIFIED: dict[AreaState, UnifiedArmingStatus] = {
    AreaState.AWAY: UnifiedArmingStatus.ARM_AWAY,
    AreaState.STAY: UnifiedArmingStatus.ARM_STAY,
    AreaState.INSTANT: UnifiedArmingStatus.ARM_INSTANT,
    AreaState.DISARMED: UnifiedArmingStatus.DISARMED,
}
_UNIFIED_TO_ARM_MODE: dict[UnifiedArmingStatus, ArmMode] = {
    UnifiedArmingStatus.ARM_AWAY: ArmMode.AWAY,
    UnifiedArmingStatus.ARM_STAY: ArmMode.STAY,
    UnifiedArmingStatus.ARM_INSTANT: ArmMode.INSTANT,
    UnifiedArmingStatus.DISARMED: ArmMode.DISARM,
}
_ZONE_STATE_TO_UNIFIED: dict[ZoneState, UnifiedZoneState] = {
    ZoneState.STANDBY: UnifiedZoneState.STANDBY,
    ZoneState.OPEN: UnifiedZoneState.ALARM,
}


class SmartLivingPinMissing(SmartLivingError):
    """A command was asked but no user PIN is configured."""


class SmartLivingAdapter:
    def __init__(self, client: Client, pin: str | None) -> None:
        self._client = client
        self._pin = pin or None
        self._zones: dict[int, UnifiedZone] = {}
        self._partitions: dict[int, UnifiedPartition] = {}
        self._refreshed_at: float | None = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        """Read the configuration (names, zones, areas): the first exchange."""
        try:
            await self._client.read_configuration()
        except SmartLivingConnectionError as err:
            raise ConnectionError(str(err)) from err
        await self._refresh(force = True)

    async def close(self) -> None:
        await self._client.close()

    @property
    def configuration(self) -> Configuration | None:
        return self._client.configuration

    @property
    def serial_number(self) -> str | None:
        config = self._client.configuration
        return config.info.serial if config is not None else None

    @property
    def panel_firmware_and_model(self) -> tuple[str | None, str | None]:
        config = self._client.configuration
        if config is None:
            return None, None
        return config.info.firmware, config.info.model

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    async def _refresh(self, force: bool = False) -> None:
        if (
            not force and self._refreshed_at is not None
            and time.monotonic() - self._refreshed_at < _STATUS_REUSE
        ):
            return
        try:
            status = await self._client.read_status()
        except SmartLivingConnectionError as err:
            raise ConnectionError(str(err)) from err
        config = self._client.configuration
        assert config is not None
        zones: dict[int, UnifiedZone] = {}
        for zone_id, zone in config.zones.items():
            zone_status = status.zones.get(zone_id)
            zones[zone_id] = UnifiedZone(
                zone_id = zone_id,
                terminal_id = zone.physical_index,
                label = zone.name or f"Zona {zone_id}",
                partitions = zone.areas,
                state = (
                    _ZONE_STATE_TO_UNIFIED.get(zone_status.state, UnifiedZoneState.UNKNOWN)
                    if zone_status is not None else None
                ),
                bypass = (not zone_status.enabled) if zone_status is not None else None,
                alarm_memory = zone_status.alarm_memory if zone_status is not None else None,
                native_status_bytes = bytes([zone_status.raw]) if zone_status is not None else None,
            )
        partitions: dict[int, UnifiedPartition] = {}
        for area_id, area_status in status.areas.items():
            if area_status.state is AreaState.NOT_CONFIGURED:
                continue
            partitions[area_id] = UnifiedPartition(
                partition_id = area_id,
                label = config.area_names.get(area_id) or f"Area {area_id}",
                zones = {z.zone_id for z in config.zones.values() if area_id in z.areas},
                arming_status = _AREA_STATE_TO_UNIFIED.get(area_status.state),
                partition_state = UnifiedPartitionState.OK,
                alarm_memory = area_status.alarm_memory,
            )
        self._zones = zones
        self._partitions = partitions
        self._refreshed_at = time.monotonic()

    @property
    def zones(self) -> MappingProxyType[int, UnifiedZone]:
        return MappingProxyType(self._zones)

    @property
    def partitions(self) -> MappingProxyType[int, UnifiedPartition]:
        return MappingProxyType(self._partitions)

    @property
    def count_bypassed_zones(self) -> int:
        return sum(1 for zone in self._zones.values() if zone.bypass)

    @property
    def count_zone_alarm_memories(self) -> int:
        return sum(1 for zone in self._zones.values() if zone.alarm_memory)

    @property
    def count_partition_alarm_memories(self) -> int:
        return sum(1 for partition in self._partitions.values() if partition.alarm_memory)

    def get_zone(self, zone_id: int) -> UnifiedZone | None:
        return self._zones.get(zone_id)

    def get_partition(self, partition_id: int) -> UnifiedPartition | None:
        return self._partitions.get(partition_id)

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

    async def update_zones(self) -> MappingProxyType[int, UnifiedZone]:
        await self._refresh()
        return self.zones

    async def update_partitions(self) -> MappingProxyType[int, UnifiedPartition]:
        await self._refresh()
        return self.partitions

    # ------------------------------------------------------------------
    # Commands (never retried by the library)
    # ------------------------------------------------------------------
    def _require_pin(self) -> str:
        if not self._pin:
            raise SmartLivingPinMissing("no user PIN configured for SmartLiving commands")
        return self._pin

    async def _command(self, coro) -> None:
        try:
            await coro
        except SmartLivingConnectionError as err:
            raise ConnectionError(str(err)) from err
        finally:
            # the next poll reads the new state
            self._refreshed_at = None

    async def set_zone_bypass(self, zone_id: int, bypass: bool) -> None:
        await self._command(self._client.set_zone_excluded(self._require_pin(), zone_id, bypass))

    async def set_all_zone_bypasses(self, bypass: bool) -> None:
        pin = self._require_pin()
        for zone_id in list(self._zones):
            await self._command(self._client.set_zone_excluded(pin, zone_id, bypass))

    async def set_partition_arming_statuses(
            self, arming_statuses: dict[int, UnifiedArmingStatus],
    ) -> None:
        modes = {pid: _UNIFIED_TO_ARM_MODE[status] for pid, status in arming_statuses.items()}
        if modes:
            await self._command(self._client.set_areas(self._require_pin(), modes))

    async def set_partition_arming_status(
            self, partition_id: int, arming_status: UnifiedArmingStatus,
    ) -> None:
        await self.set_partition_arming_statuses({partition_id: arming_status})

    async def set_all_partitions_arming_status(self, arming_status: UnifiedArmingStatus) -> None:
        await self.set_partition_arming_statuses({pid: arming_status for pid in self._partitions})

    async def reset_partition_memories(self, partition_ids: set[int]) -> None:
        if partition_ids:
            await self._command(self._client.reset_alarm_memory(self._require_pin(), set(partition_ids)))

    async def reset_partition_memory(self, partition_id: int) -> None:
        await self.reset_partition_memories({partition_id})

    async def reset_all_partition_memories(self) -> None:
        await self.reset_partition_memories(set(self._partitions))
