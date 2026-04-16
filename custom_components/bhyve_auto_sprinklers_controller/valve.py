"""Valve entities for B-hyve sprinkler zones."""

from __future__ import annotations

from homeassistant.components.valve import (
    ValveDeviceClass,
    ValveEntity,
    ValveEntityFeature,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_QUICK_RUN_DURATION
from .entity import BhyveZoneCoordinatorEntity
from .models import BhyveSprinklersConfigEntry


async def async_setup_entry(
    hass,
    entry: BhyveSprinklersConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up zone valve entities."""

    del hass
    entities: list[ValveEntity] = []
    for controller in entry.runtime_data.coordinator.data.controllers:
        for zone in controller.zones:
            entities.append(
                BhyveSprinklerZoneValve(
                    entry,
                    controller.device_id,
                    zone.zone_number,
                )
            )

    async_add_entities(entities)


class BhyveSprinklerZoneValve(BhyveZoneCoordinatorEntity, ValveEntity):
    """Valve entity that starts and stops B-hyve quick runs."""

    _attr_assumed_state = True
    _attr_device_class = ValveDeviceClass.WATER
    _attr_has_entity_name = True
    _attr_supported_features = (
        ValveEntityFeature.OPEN
        | ValveEntityFeature.CLOSE
        | ValveEntityFeature.STOP
    )
    _attr_reports_position = False

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the valve entity."""

        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"{device_id}_{zone_number}_valve"

    @property
    def name(self) -> str | None:
        """Return the entity name."""

        zone = self.zone
        if zone is None:
            return f"Zone {self._zone_number}"
        return zone.name

    @property
    def is_closed(self) -> bool | None:
        """Return whether the zone is currently watering."""

        controller = self.controller
        if controller is None:
            return None

        active_run = controller.active_run
        return active_run is None or active_run.zone_number != self._zone_number

    @property
    def extra_state_attributes(self) -> dict[str, str | int | float | bool]:
        """Return useful zone metadata."""

        zone = self.zone
        if zone is None:
            return {}

        attributes: dict[str, str | int | float | bool] = {
            "device_id": zone.device_id,
            "zone_id": zone.zone_id,
            "zone_number": zone.zone_number,
            "enabled": zone.enabled,
            "quickrun_duration": self._entry.runtime_data.quick_run_durations.get(
                self._duration_key,
                DEFAULT_QUICK_RUN_DURATION,
            ),
        }
        if zone.crop_type:
            attributes["crop_type"] = zone.crop_type
        if zone.exposure_type:
            attributes["exposure_type"] = zone.exposure_type
        if zone.soil_type:
            attributes["soil_type"] = zone.soil_type
        if zone.slope_type:
            attributes["slope_type"] = zone.slope_type
        if zone.nozzle_type:
            attributes["nozzle_type"] = zone.nozzle_type
        if zone.flow_rate is not None:
            attributes["flow_rate"] = zone.flow_rate
        if zone.efficiency is not None:
            attributes["efficiency"] = zone.efficiency
        if zone.smart_duration is not None:
            attributes["smart_duration"] = zone.smart_duration
        if zone.quickrun_duration is not None:
            attributes["bhyve_native_quickrun_duration"] = zone.quickrun_duration
        if zone.latest_event is not None:
            if zone.latest_event.duration is not None:
                attributes["last_duration"] = zone.latest_event.duration
            if zone.latest_event.end_local is not None:
                attributes["last_end_local"] = zone.latest_event.end_local
            if zone.latest_event.schedule_name is not None:
                attributes["last_schedule_name"] = zone.latest_event.schedule_name
            if zone.latest_event.schedule_type is not None:
                attributes["last_schedule_type"] = zone.latest_event.schedule_type
        if zone.zone_disable_reason:
            attributes["zone_disable_reason"] = zone.zone_disable_reason
        return attributes

    async def async_open_valve(self) -> None:
        """Start a quick run for this zone."""

        zone = self.zone
        if zone is None:
            raise HomeAssistantError("Zone is not available")
        if not zone.enabled:
            raise HomeAssistantError(f"Zone '{zone.name}' is disabled in B-hyve")

        plan_coordinator = self._entry.runtime_data.plan_coordinator
        if plan_coordinator is not None:
            await plan_coordinator.async_cancel_automatic_cycle(self._device_id)
        await self.coordinator.async_quick_run_zone(
            self._device_id,
            self._zone_number,
            DEFAULT_QUICK_RUN_DURATION,
        )

    async def async_close_valve(self) -> None:
        """Stop watering if this zone is the active zone."""

        await self._async_stop_zone()

    async def async_stop_valve(self) -> None:
        """Stop watering if this zone is the active zone."""

        await self._async_stop_zone()

    async def _async_stop_zone(self) -> None:
        """Stop the current run when it belongs to this zone."""

        controller = self.controller
        if controller is None:
            raise HomeAssistantError("Sprinkler controller is not available")

        active_run = controller.active_run
        if active_run is None:
            return
        if active_run.zone_number != self._zone_number:
            raise HomeAssistantError(
                f"Zone {active_run.zone_number} is currently running on this controller"
            )

        plan_coordinator = self._entry.runtime_data.plan_coordinator
        if plan_coordinator is not None:
            await plan_coordinator.async_cancel_automatic_cycle(self._device_id)
        await self.coordinator.async_stop_watering(self._device_id)

    @property
    def _duration_key(self) -> str:
        """Return the shared runtime-data key for this zone."""

        return f"{self._device_id}:{self._zone_number}"
