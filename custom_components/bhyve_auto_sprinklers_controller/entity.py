"""Shared entity helpers for B-hyve Auto Sprinklers Controller."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .models import (
    BhyveControllerPlan,
    BhyveIrrigationSnapshot,
    BhyveIrrigationPlanSnapshot,
    BhyveSprinklerControllerSnapshot,
    BhyveSprinklerZone,
    BhyveZonePlan,
    BhyveSprinklersConfigEntry,
)


class BhyveControllerCoordinatorEntity(CoordinatorEntity[BhyveIrrigationSnapshot]):
    """Base entity for controller-level coordinator entities."""

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
    ) -> None:
        """Initialize the base controller entity."""

        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._device_id = device_id

    @property
    def controller(self) -> BhyveSprinklerControllerSnapshot | None:
        """Return the latest controller snapshot."""

        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        for controller in snapshot.controllers:
            if controller.device_id == self._device_id:
                return controller
        return None

    @property
    def available(self) -> bool:
        """Return True when the controller still exists."""

        return super().available and self.controller is not None

    @property
    def device_info(self) -> DeviceInfo:
        """Return the controller device info."""

        controller = self.controller
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer="Orbit B-hyve",
            model=(
                controller.product_model
                if controller is not None and controller.product_model
                else "Sprinkler Controller"
            ),
            name=(
                controller.nickname
                if controller is not None and controller.nickname
                else "B-hyve Sprinkler Controller"
            ),
            via_device=(DOMAIN, self._entry.entry_id),
        )


class BhyveZoneCoordinatorEntity(CoordinatorEntity[BhyveIrrigationSnapshot]):
    """Base entity for zone-level entities."""

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the base entity."""

        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._device_id = device_id
        self._zone_number = zone_number

    @property
    def available(self) -> bool:
        """Return True when both the coordinator and the zone are available."""

        return super().available and self.controller is not None and self.zone is not None

    @property
    def controller(self) -> BhyveSprinklerControllerSnapshot | None:
        """Return the latest controller snapshot."""

        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        for controller in snapshot.controllers:
            if controller.device_id == self._device_id:
                return controller
        return None

    @property
    def zone(self) -> BhyveSprinklerZone | None:
        """Return the latest zone snapshot."""

        controller = self.controller
        if controller is None:
            return None

        for zone in controller.zones:
            if zone.zone_number == self._zone_number:
                return zone
        return None

    @property
    def device_info(self) -> DeviceInfo:
        """Return the controller device info."""

        controller = self.controller
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer="Orbit B-hyve",
            model=(
                controller.product_model
                if controller is not None and controller.product_model
                else "Sprinkler Controller"
            ),
            name=(
                controller.nickname
                if controller is not None and controller.nickname
                else "B-hyve Sprinkler Controller"
            ),
            via_device=(DOMAIN, self._entry.entry_id),
        )


class BhyveControllerPlanCoordinatorEntity(
    CoordinatorEntity[BhyveIrrigationPlanSnapshot],
):
    """Base entity for controller-level planning entities."""

    def __init__(self, entry: BhyveSprinklersConfigEntry, device_id: str) -> None:
        """Initialize the planning base entity."""

        super().__init__(entry.runtime_data.plan_coordinator)
        self._entry = entry
        self._device_id = device_id

    @property
    def controller_plan(self) -> BhyveControllerPlan | None:
        """Return the current controller plan."""

        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        for controller in snapshot.controllers:
            if controller.device_id == self._device_id:
                return controller
        return None

    @property
    def available(self) -> bool:
        """Return True when the controller plan exists."""

        return super().available and self.controller_plan is not None

    @property
    def device_info(self) -> DeviceInfo:
        """Attach planning entities to the sprinkler controller device."""

        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer="Orbit B-hyve",
            model=(
                self.controller_plan.product_model
                if self.controller_plan is not None and self.controller_plan.product_model
                else "Sprinkler Controller"
            ),
            name=(
                self.controller_plan.nickname
                if self.controller_plan is not None
                else "B-hyve Sprinkler Controller"
            ),
            via_device=(DOMAIN, self._entry.entry_id),
        )


class BhyveZonePlanCoordinatorEntity(BhyveControllerPlanCoordinatorEntity):
    """Base entity for zone-level planning entities."""

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the planning zone entity."""

        super().__init__(entry, device_id)
        self._zone_number = zone_number

    @property
    def zone_plan(self) -> BhyveZonePlan | None:
        """Return the current zone plan."""

        controller_plan = self.controller_plan
        if controller_plan is None:
            return None
        for zone_plan in controller_plan.zone_plans:
            if zone_plan.zone_number == self._zone_number:
                return zone_plan
        return None

    @property
    def available(self) -> bool:
        """Return True when the zone plan exists."""

        return super().available and self.zone_plan is not None
