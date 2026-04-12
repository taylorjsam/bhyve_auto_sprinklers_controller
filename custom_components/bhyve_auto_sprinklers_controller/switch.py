"""Switch entities for irrigation automation controls."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEFAULT_AUTOMATIC_WATERING_ENABLED,
    DEFAULT_AUTOMATIC_WINDOW_ENABLED,
    DEFAULT_NOTIFICATIONS_ENABLED,
    DOMAIN,
)
from .models import BhyveIrrigationSnapshot, BhyveSprinklerControllerSnapshot, BhyveSprinklersConfigEntry


async def async_setup_entry(
    hass,
    entry: BhyveSprinklersConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up automation-control switches."""

    del hass
    entities: list[SwitchEntity] = [
        BhyveAutomaticWateringSwitch(entry),
        BhyveNotificationsEnabledSwitch(entry),
    ]
    for controller in entry.runtime_data.coordinator.data.controllers:
        entities.append(BhyveAutomaticWindowSwitch(entry, controller.device_id))

    async_add_entities(entities)


class _BhyveAccountRestoreSwitch(
    CoordinatorEntity[BhyveIrrigationSnapshot],
    RestoreEntity,
    SwitchEntity,
):
    """Base persisted switch attached to the B-hyve account device."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True

    def __init__(self, entry: BhyveSprinklersConfigEntry) -> None:
        """Initialize the account switch."""

        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._is_on = False

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the switch to the account device."""

        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            manufacturer="Orbit B-hyve",
            model="Cloud Account",
            name="B-hyve Account",
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last state when available."""

        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._is_on = last_state.state == "on"
        self._store()

    @property
    def is_on(self) -> bool:
        """Return the current switch value."""

        return self._is_on

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the switch on."""

        del kwargs
        self._is_on = True
        self._store()
        self.async_write_ha_state()
        await self._async_after_change()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the switch off."""

        del kwargs
        self._is_on = False
        self._store()
        self.async_write_ha_state()
        await self._async_after_change()

    async def _async_after_change(self) -> None:
        """Hook for subclasses when the value changes."""

    def _store(self) -> None:
        """Persist the current switch value into runtime data."""


class BhyveAutomaticWateringSwitch(_BhyveAccountRestoreSwitch):
    """Master toggle for future automated watering behavior."""

    _attr_icon = "mdi:calendar-sync"
    _attr_name = "Automatic watering"

    def __init__(self, entry: BhyveSprinklersConfigEntry) -> None:
        """Initialize the switch."""

        super().__init__(entry)
        self._attr_unique_id = f"{entry.entry_id}_automatic_watering"
        self._is_on = DEFAULT_AUTOMATIC_WATERING_ENABLED

    def _store(self) -> None:
        """Persist the current switch value into runtime data."""

        self._entry.runtime_data.automatic_watering_enabled = self._is_on


class BhyveNotificationsEnabledSwitch(_BhyveAccountRestoreSwitch):
    """Toggle for irrigation-plan push notifications."""

    _attr_icon = "mdi:bell-badge"
    _attr_name = "Notifications enabled"

    def __init__(self, entry: BhyveSprinklersConfigEntry) -> None:
        """Initialize the switch."""

        super().__init__(entry)
        self._attr_unique_id = f"{entry.entry_id}_notifications_enabled"
        self._is_on = DEFAULT_NOTIFICATIONS_ENABLED

    def _store(self) -> None:
        """Persist the current switch value into runtime data."""

        self._entry.runtime_data.notifications_enabled = self._is_on


class BhyveAutomaticWindowSwitch(
    CoordinatorEntity[BhyveIrrigationSnapshot],
    RestoreEntity,
    SwitchEntity,
):
    """Use the planner's suggested watering window for a controller."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-time-eight-outline"
    _attr_name = "Use automatic watering window"

    def __init__(self, entry: BhyveSprinklersConfigEntry, device_id: str) -> None:
        """Initialize the controller switch."""

        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_automatic_watering_window"
        self._is_on = DEFAULT_AUTOMATIC_WINDOW_ENABLED

    async def async_added_to_hass(self) -> None:
        """Restore the last state when available."""

        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._is_on = last_state.state == "on"
        self._store()

    @property
    def available(self) -> bool:
        """Return True when the controller still exists."""

        return super().available and self._controller is not None

    @property
    def is_on(self) -> bool:
        """Return the current switch value."""

        return self._is_on

    async def async_turn_on(self, **kwargs) -> None:
        """Enable the automatic watering window."""

        del kwargs
        self._is_on = True
        self._store()
        self.async_write_ha_state()
        await self._entry.runtime_data.plan_coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Disable the automatic watering window and use manual times."""

        del kwargs
        self._is_on = False
        self._store()
        self.async_write_ha_state()
        await self._entry.runtime_data.plan_coordinator.async_request_refresh()

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the switch to the sprinkler controller."""

        controller = self._controller
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer="Orbit B-hyve",
            model=controller.product_model if controller else "Sprinkler Controller",
            name=controller.nickname if controller else "B-hyve Sprinkler Controller",
            via_device=(DOMAIN, self._entry.entry_id),
        )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Explain how the switch affects the planner."""

        return {
            "automatic_when_on": "Suggested start/end times from crop mix and season",
            "manual_when_off": "Use the controller's Watering start time and Watering end time entities",
        }

    @property
    def _controller(self) -> BhyveSprinklerControllerSnapshot | None:
        """Return the latest controller snapshot."""

        for controller in self.coordinator.data.controllers:
            if controller.device_id == self._device_id:
                return controller
        return None

    def _store(self) -> None:
        """Persist the current switch value into runtime data."""

        self._entry.runtime_data.automatic_window_enabled[self._device_id] = self._is_on
