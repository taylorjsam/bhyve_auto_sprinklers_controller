"""Time entities for controller-level watering windows."""

from __future__ import annotations

from datetime import time as dt_time

from homeassistant.components.time import TimeEntity
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEFAULT_WATERING_END_TIME,
    DEFAULT_WATERING_START_TIME,
    DOMAIN,
)
from .models import (
    BhyveIrrigationSnapshot,
    BhyveSprinklerControllerSnapshot,
    BhyveSprinklersConfigEntry,
)


async def async_setup_entry(
    hass,
    entry: BhyveSprinklersConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up controller watering-window time entities."""

    del hass
    entities: list[TimeEntity] = []
    for controller in entry.runtime_data.coordinator.data.controllers:
        entities.append(
            BhyveControllerWateringTimeEntity(
                entry,
                controller.device_id,
                key="start",
                default_value=DEFAULT_WATERING_START_TIME,
                entity_name="Watering start time",
                icon="mdi:clock-start",
            )
        )
        entities.append(
            BhyveControllerWateringTimeEntity(
                entry,
                controller.device_id,
                key="end",
                default_value=DEFAULT_WATERING_END_TIME,
                entity_name="Watering end time",
                icon="mdi:clock-end",
            )
        )

    async_add_entities(entities)


class BhyveControllerWateringTimeEntity(
    CoordinatorEntity[BhyveIrrigationSnapshot],
    RestoreEntity,
    TimeEntity,
):
    """Persisted watering-window time for a controller."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        *,
        key: str,
        default_value: dt_time,
        entity_name: str,
        icon: str,
    ) -> None:
        """Initialize the watering-window time entity."""

        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._device_id = device_id
        self._key = key
        self._value = default_value
        self._attr_name = entity_name
        self._attr_icon = icon
        self._attr_unique_id = f"{device_id}_watering_{key}_time"

    async def async_added_to_hass(self) -> None:
        """Restore the previous configured time if one exists."""

        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            self._entry.runtime_data.watering_window_times[self._runtime_key] = self._value
            return

        try:
            self._value = dt_time.fromisoformat(last_state.state)
        except ValueError:
            self._entry.runtime_data.watering_window_times[self._runtime_key] = self._value
            return

        self._entry.runtime_data.watering_window_times[self._runtime_key] = self._value

    @property
    def available(self) -> bool:
        """Return True when the controller still exists in coordinator data."""

        return super().available and self._controller is not None

    @property
    def native_value(self) -> dt_time:
        """Return the configured watering-window time."""

        return self._value

    async def async_set_value(self, value: dt_time) -> None:
        """Set the configured watering-window time."""

        self._value = value
        self._entry.runtime_data.watering_window_times[self._runtime_key] = self._value
        self.async_write_ha_state()
        if self._entry.runtime_data.plan_coordinator is not None:
            await self._entry.runtime_data.plan_coordinator.async_request_refresh()

    @property
    def device_info(self) -> DeviceInfo:
        """Return controller device info for this time entity."""

        controller = self._controller
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

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose the stored watering-window role for later automations."""

        return {
            "watering_window_role": self._key,
            "used_when": "Use automatic watering window is off",
        }

    @property
    def _controller(self) -> BhyveSprinklerControllerSnapshot | None:
        """Return the latest controller snapshot."""

        for controller in self.coordinator.data.controllers:
            if controller.device_id == self._device_id:
                return controller
        return None

    @property
    def _runtime_key(self) -> str:
        """Return the shared runtime-data key for this controller time."""

        return f"{self._device_id}:{self._key}"
