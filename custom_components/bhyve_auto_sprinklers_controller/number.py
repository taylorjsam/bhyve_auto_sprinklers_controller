"""Number entities for B-hyve sprinkler zones."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, RestoreNumber
from homeassistant.const import UnitOfLength, UnitOfSpeed, UnitOfTemperature, UnitOfTime
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEFAULT_MAX_AUTOMATIC_WATERING_WINDOW_MINUTES,
    DEFAULT_MAX_WEEKLY_RUN_TIME,
    DEFAULT_MAX_WATERING_WIND_SPEED_MPH,
    DEFAULT_MINIMUM_RUN_THRESHOLD_MINUTES,
    DEFAULT_MIN_WATERING_TEMPERATURE_F,
    DEFAULT_OVERALL_WATERING_COEFFICIENT,
    DEFAULT_QUICK_RUN_DURATION,
    DEFAULT_ZONE_APPLICATION_RATE_IN_PER_HOUR,
    DEFAULT_ZONE_TRIGGER_BUFFER_INCHES,
    DEFAULT_ZONE_WATERING_COEFFICIENT,
    DOMAIN,
    MAX_ZONE_KC,
    MAX_ZONE_MAD,
    MAX_ZONE_ROOT_DEPTH_IN,
    MAX_ZONE_SOIL_WHC_IN_PER_IN,
    MAX_AUTOMATIC_WATERING_WINDOW_MINUTES,
    MAX_MINIMUM_RUN_THRESHOLD_MINUTES,
    MAX_ZONE_APPLICATION_RATE_IN_PER_HOUR,
    MAX_ZONE_TRIGGER_BUFFER_INCHES,
    MAX_WATERING_TEMPERATURE_F,
    MAX_WATERING_WIND_SPEED_MPH,
    MAX_WEEKLY_RUN_TIME,
    MAX_QUICK_RUN_DURATION,
    MAX_ZONE_WATERING_COEFFICIENT,
    MIN_AUTOMATIC_WATERING_WINDOW_MINUTES,
    MIN_ZONE_KC,
    MIN_ZONE_MAD,
    MIN_ZONE_ROOT_DEPTH_IN,
    MIN_ZONE_SOIL_WHC_IN_PER_IN,
    MIN_ZONE_TRIGGER_BUFFER_INCHES,
    MIN_WATERING_TEMPERATURE_F,
    MIN_QUICK_RUN_DURATION,
    MIN_ZONE_WATERING_COEFFICIENT,
    ZONE_AGRONOMY_DEFAULTS,
    normalize_zone_watering_profile,
)
from .entity import BhyveControllerCoordinatorEntity, BhyveZoneCoordinatorEntity
from .models import BhyveIrrigationSnapshot, BhyveSprinklersConfigEntry


async def async_setup_entry(
    hass,
    entry: BhyveSprinklersConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up quick-run duration entities for each zone."""

    del hass
    entities: list[NumberEntity] = [
        BhyveOverallWateringCoefficientNumber(entry),
        BhyveMinimumRunThresholdNumber(entry),
        BhyveMaxWateringWindSpeedNumber(entry),
        BhyveMinimumWateringTemperatureNumber(entry),
    ]
    for controller in entry.runtime_data.coordinator.data.controllers:
        entities.append(
            BhyveControllerMaximumAutomaticWindowNumber(
                entry,
                controller.device_id,
            )
        )
        for zone in controller.zones:
            entities.append(
                BhyveZoneApplicationRateNumber(
                    entry,
                    controller.device_id,
                    zone.zone_number,
                )
            )
            entities.append(
                BhyveZoneRootDepthNumber(
                    entry,
                    controller.device_id,
                    zone.zone_number,
                )
            )
            entities.append(
                BhyveZoneSoilWHCNumber(
                    entry,
                    controller.device_id,
                    zone.zone_number,
                )
            )
            entities.append(
                BhyveZoneMADNumber(
                    entry,
                    controller.device_id,
                    zone.zone_number,
                )
            )
            entities.append(
                BhyveZoneKCNumber(
                    entry,
                    controller.device_id,
                    zone.zone_number,
                )
            )
            entities.append(
                BhyveZoneTriggerBufferNumber(
                    entry,
                    controller.device_id,
                    zone.zone_number,
                )
            )
            entities.append(
                BhyveZoneWateringCoefficientNumber(
                    entry,
                    controller.device_id,
                    zone.zone_number,
                )
            )
            entities.append(
                BhyveZoneMaxWeeklyRunTimeNumber(
                    entry,
                    controller.device_id,
                    zone.zone_number,
                )
            )
            entities.append(
                BhyveZoneQuickRunDurationNumber(
                    entry,
                    controller.device_id,
                    zone.zone_number,
                )
            )

    async_add_entities(entities)


class BhyveOverallWateringCoefficientNumber(
    CoordinatorEntity[BhyveIrrigationSnapshot],
    RestoreNumber,
    NumberEntity,
):
    """Global watering coefficient used to scale future calculations."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_icon = "mdi:tune-variant"
    _attr_mode = "box"
    _attr_name = "Overall watering coefficient"
    _attr_native_min_value = 0.1
    _attr_native_max_value = 3.0
    _attr_native_step = 0.1

    def __init__(self, entry: BhyveSprinklersConfigEntry) -> None:
        """Initialize the overall watering coefficient number."""

        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_overall_watering_coefficient"
        self._value = DEFAULT_OVERALL_WATERING_COEFFICIENT

    async def async_added_to_hass(self) -> None:
        """Restore the previous watering coefficient if one exists."""

        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        restored = False
        if last_number_data is not None and last_number_data.native_value is not None:
            self._value = round(
                max(0.1, min(3.0, float(last_number_data.native_value))),
                1,
            )
            restored = True
        self._entry.runtime_data.overall_watering_coefficient = self._value
        if restored and self._entry.runtime_data.plan_coordinator is not None:
            self.hass.async_create_task(_async_request_plan_refresh(self._entry))

    @property
    def native_value(self) -> float:
        """Return the stored overall watering coefficient."""

        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Set the stored overall watering coefficient."""

        self._value = max(0.1, min(3.0, round(float(value), 1)))
        self._entry.runtime_data.overall_watering_coefficient = self._value
        self.async_write_ha_state()
        await _async_request_plan_refresh(self._entry)

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the entity to the B-hyve account device."""

        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            manufacturer="Orbit B-hyve",
            model="Cloud Account",
            name="B-hyve Account",
        )


class BhyveMinimumRunThresholdNumber(
    CoordinatorEntity[BhyveIrrigationSnapshot],
    RestoreNumber,
    NumberEntity,
):
    """Global minimum runtime required before a zone should actually water."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_icon = "mdi:timer-outline"
    _attr_mode = "box"
    _attr_name = "Minimum run threshold"
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_MINIMUM_RUN_THRESHOLD_MINUTES
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, entry: BhyveSprinklersConfigEntry) -> None:
        """Initialize the minimum-run-threshold number."""

        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_minimum_run_threshold"
        self._value = float(DEFAULT_MINIMUM_RUN_THRESHOLD_MINUTES)

    async def async_added_to_hass(self) -> None:
        """Restore the previous threshold if one exists."""

        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        if last_number_data is not None and last_number_data.native_value is not None:
            self._value = max(
                0.0,
                min(MAX_MINIMUM_RUN_THRESHOLD_MINUTES, float(last_number_data.native_value)),
            )
        self._entry.runtime_data.minimum_run_threshold_minutes = int(round(self._value))

    @property
    def native_value(self) -> float:
        """Return the minimum runtime threshold."""

        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Set the minimum runtime threshold used by the planner."""

        self._value = float(
            max(0, min(MAX_MINIMUM_RUN_THRESHOLD_MINUTES, round(float(value))))
        )
        self._entry.runtime_data.minimum_run_threshold_minutes = int(round(self._value))
        self.async_write_ha_state()
        await _async_request_plan_refresh(self._entry)

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the entity to the B-hyve account device."""

        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            manufacturer="Orbit B-hyve",
            model="Cloud Account",
            name="B-hyve Account",
        )


class BhyveMaxWateringWindSpeedNumber(
    CoordinatorEntity[BhyveIrrigationSnapshot],
    RestoreNumber,
    NumberEntity,
):
    """Maximum wind speed before the planner weather-holds watering."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_icon = "mdi:weather-windy"
    _attr_mode = "box"
    _attr_name = "Maximum watering wind speed"
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_WATERING_WIND_SPEED_MPH
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfSpeed.MILES_PER_HOUR

    def __init__(self, entry: BhyveSprinklersConfigEntry) -> None:
        """Initialize the wind-speed hold threshold."""

        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_max_watering_wind_speed"
        self._value = float(DEFAULT_MAX_WATERING_WIND_SPEED_MPH)

    async def async_added_to_hass(self) -> None:
        """Restore the previous threshold if one exists."""

        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        if last_number_data is not None and last_number_data.native_value is not None:
            self._value = max(
                0.0,
                min(MAX_WATERING_WIND_SPEED_MPH, float(last_number_data.native_value)),
            )
        self._entry.runtime_data.max_watering_wind_speed_mph = float(self._value)

    @property
    def native_value(self) -> float:
        """Return the maximum watering wind speed."""

        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Set the maximum wind speed the planner should allow."""

        self._value = float(max(0, min(MAX_WATERING_WIND_SPEED_MPH, round(float(value)))))
        self._entry.runtime_data.max_watering_wind_speed_mph = float(self._value)
        self.async_write_ha_state()
        await _async_request_plan_refresh(self._entry)

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the entity to the B-hyve account device."""

        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            manufacturer="Orbit B-hyve",
            model="Cloud Account",
            name="B-hyve Account",
        )


class BhyveMinimumWateringTemperatureNumber(
    CoordinatorEntity[BhyveIrrigationSnapshot],
    RestoreNumber,
    NumberEntity,
):
    """Minimum temperature required before the planner allows watering."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_icon = "mdi:snowflake-thermometer"
    _attr_mode = "box"
    _attr_name = "Minimum watering temperature"
    _attr_native_min_value = MIN_WATERING_TEMPERATURE_F
    _attr_native_max_value = MAX_WATERING_TEMPERATURE_F
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT

    def __init__(self, entry: BhyveSprinklersConfigEntry) -> None:
        """Initialize the minimum temperature hold threshold."""

        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_minimum_watering_temperature"
        self._value = float(DEFAULT_MIN_WATERING_TEMPERATURE_F)

    async def async_added_to_hass(self) -> None:
        """Restore the previous threshold if one exists."""

        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        if last_number_data is not None and last_number_data.native_value is not None:
            self._value = max(
                MIN_WATERING_TEMPERATURE_F,
                min(MAX_WATERING_TEMPERATURE_F, float(last_number_data.native_value)),
            )
        self._entry.runtime_data.min_watering_temperature_f = float(self._value)

    @property
    def native_value(self) -> float:
        """Return the minimum watering temperature."""

        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Set the minimum temperature the planner should allow."""

        self._value = float(
            max(
                MIN_WATERING_TEMPERATURE_F,
                min(MAX_WATERING_TEMPERATURE_F, round(float(value))),
            )
        )
        self._entry.runtime_data.min_watering_temperature_f = float(self._value)
        self.async_write_ha_state()
        await _async_request_plan_refresh(self._entry)

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the entity to the B-hyve account device."""

        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            manufacturer="Orbit B-hyve",
            model="Cloud Account",
            name="B-hyve Account",
        )


class BhyveControllerMaximumAutomaticWindowNumber(
    BhyveControllerCoordinatorEntity,
    RestoreNumber,
    NumberEntity,
):
    """Maximum automatic watering window the planner may use for a controller."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-time-eight-outline"
    _attr_mode = "box"
    _attr_name = "Maximum automatic watering window"
    _attr_native_min_value = MIN_AUTOMATIC_WATERING_WINDOW_MINUTES
    _attr_native_max_value = MAX_AUTOMATIC_WATERING_WINDOW_MINUTES
    _attr_native_step = 15
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
    ) -> None:
        """Initialize the controller automatic-window cap."""

        super().__init__(entry, device_id)
        self._attr_unique_id = f"{device_id}_maximum_automatic_watering_window"
        self._value = float(DEFAULT_MAX_AUTOMATIC_WATERING_WINDOW_MINUTES)

    async def async_added_to_hass(self) -> None:
        """Restore the previous window cap when available."""

        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        if last_number_data is not None and last_number_data.native_value is not None:
            self._value = float(
                max(
                    MIN_AUTOMATIC_WATERING_WINDOW_MINUTES,
                    min(
                        MAX_AUTOMATIC_WATERING_WINDOW_MINUTES,
                        round(float(last_number_data.native_value) / 15) * 15,
                    ),
                )
            )
        self._entry.runtime_data.automatic_window_max_minutes[self._device_id] = int(
            round(self._value)
        )

    @property
    def native_value(self) -> float:
        """Return the configured maximum automatic window in minutes."""

        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Update the controller automatic-window cap."""

        rounded = int(round(float(value) / 15) * 15)
        self._value = float(
            max(
                MIN_AUTOMATIC_WATERING_WINDOW_MINUTES,
                min(MAX_AUTOMATIC_WATERING_WINDOW_MINUTES, rounded),
            )
        )
        self._entry.runtime_data.automatic_window_max_minutes[self._device_id] = int(
            round(self._value)
        )
        self.async_write_ha_state()
        await _async_request_plan_refresh(self._entry)


class BhyveZoneQuickRunDurationNumber(
    BhyveZoneCoordinatorEntity,
    RestoreNumber,
    NumberEntity,
):
    """Configurable quick-run duration for a zone."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:timer-cog-outline"
    _attr_mode = "box"
    _attr_native_min_value = MIN_QUICK_RUN_DURATION
    _attr_native_max_value = MAX_QUICK_RUN_DURATION
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the zone quick-run number."""

        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"{device_id}_{zone_number}_quick_run_duration"
        self._value = float(DEFAULT_QUICK_RUN_DURATION)

    async def async_added_to_hass(self) -> None:
        """Restore the previous quick-run duration if one exists."""

        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        if last_number_data is not None and last_number_data.native_value is not None:
            self._value = self._restore_quick_run_duration(
                float(last_number_data.native_value)
            )

        self._store_runtime_value()

    @property
    def name(self) -> str | None:
        """Return the entity name."""

        zone = self.zone
        if zone is None:
            return f"Zone {self._zone_number} quick run duration"
        return f"{zone.name} quick run duration"

    @property
    def native_value(self) -> float:
        """Return the configured quick-run duration."""

        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Set the quick-run duration."""

        self._value = round(value)
        self._store_runtime_value()
        self.async_write_ha_state()

    def _store_runtime_value(self) -> None:
        """Share the selected quick-run duration with the valve entities."""

        self._entry.runtime_data.quick_run_durations[self._duration_key] = int(self._value)

    def _restore_quick_run_duration(self, restored_value: float) -> float:
        """Restore user values while migrating the old one-minute B-hyve default."""

        zone = self.zone
        if (
            round(restored_value) == 60
            and zone is not None
            and zone.quickrun_duration == 60
        ):
            return float(DEFAULT_QUICK_RUN_DURATION)
        return restored_value

    @property
    def _duration_key(self) -> str:
        """Return the shared runtime-data key for this zone."""

        return f"{self._device_id}:{self._zone_number}"


class BhyveZoneApplicationRateNumber(
    BhyveZoneCoordinatorEntity,
    RestoreNumber,
    NumberEntity,
):
    """Persisted measured application rate used by the planner for a zone."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:waves-arrow-right"
    _attr_mode = "box"
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_ZONE_APPLICATION_RATE_IN_PER_HOUR
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = "in/hr"

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the zone application-rate number."""

        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"{device_id}_{zone_number}_application_rate"
        self._value = float(DEFAULT_ZONE_APPLICATION_RATE_IN_PER_HOUR)

    async def async_added_to_hass(self) -> None:
        """Restore the previous measured application rate if one exists."""

        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        if last_number_data is not None and last_number_data.native_value is not None:
            self._value = float(
                max(
                    0.0,
                    min(
                        MAX_ZONE_APPLICATION_RATE_IN_PER_HOUR,
                        float(last_number_data.native_value),
                    ),
                )
            )
        self._entry.runtime_data.zone_application_rates[self._runtime_key] = round(
            self._value,
            2,
        )

    @property
    def name(self) -> str | None:
        """Return the entity name."""

        zone = self.zone
        if zone is None:
            return f"Zone {self._zone_number} application rate"
        return f"{zone.name} application rate"

    @property
    def native_value(self) -> float:
        """Return the stored measured application rate in inches per hour."""

        return float(
            self._entry.runtime_data.zone_application_rates.get(
                self._runtime_key,
                self._value,
            )
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set the measured application rate used by the planner."""

        self._value = round(
            max(0.0, min(MAX_ZONE_APPLICATION_RATE_IN_PER_HOUR, float(value))),
            2,
        )
        self._entry.runtime_data.zone_application_rates[self._runtime_key] = self._value
        self.async_write_ha_state()
        self.coordinator.async_update_listeners()
        await _async_request_plan_refresh(self._entry)

    @property
    def _runtime_key(self) -> str:
        """Return the shared runtime-data key for this zone."""

        return f"{self._device_id}:{self._zone_number}"


class _BhyveZoneAgronomyNumber(
    BhyveZoneCoordinatorEntity,
    RestoreNumber,
    NumberEntity,
):
    """Base class for restore-backed zone agronomy numbers."""

    _value = 0.0

    @property
    def _runtime_key(self) -> str:
        """Return the shared runtime-data key for this zone."""

        return f"{self._device_id}:{self._zone_number}"

    def _default_profile_values(self) -> dict[str, float]:
        """Return the active profile default values for the zone."""

        profile = normalize_zone_watering_profile(
            self._entry.runtime_data.zone_watering_profiles.get(self._runtime_key)
        )
        return ZONE_AGRONOMY_DEFAULTS.get(
            profile,
            ZONE_AGRONOMY_DEFAULTS[normalize_zone_watering_profile(None)],
        )


class BhyveZoneRootDepthNumber(_BhyveZoneAgronomyNumber):
    """Persisted usable root depth for a single zone."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:arrow-expand-down"
    _attr_mode = "box"
    _attr_native_min_value = MIN_ZONE_ROOT_DEPTH_IN
    _attr_native_max_value = MAX_ZONE_ROOT_DEPTH_IN
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfLength.INCHES

    def __init__(self, entry: BhyveSprinklersConfigEntry, device_id: str, zone_number: int) -> None:
        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"{device_id}_{zone_number}_root_depth"
        self._value = self._default_profile_values()["root_depth_in"]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        stored_value = self._entry.runtime_data.zone_root_depths.get(self._runtime_key)
        if stored_value is not None:
            self._value = float(stored_value)
        elif last_number_data is not None and last_number_data.native_value is not None:
            self._value = float(last_number_data.native_value)
        else:
            self._value = self._default_profile_values()["root_depth_in"]
        self._entry.runtime_data.zone_root_depths[self._runtime_key] = float(self._value)

    @property
    def name(self) -> str | None:
        zone = self.zone
        if zone is None:
            return f"Zone {self._zone_number} root depth"
        return f"{zone.name} root depth"

    @property
    def native_value(self) -> float:
        return float(self._entry.runtime_data.zone_root_depths.get(self._runtime_key, self._value))

    async def async_set_native_value(self, value: float) -> None:
        self._value = float(max(MIN_ZONE_ROOT_DEPTH_IN, min(MAX_ZONE_ROOT_DEPTH_IN, round(float(value), 2))))
        self._entry.runtime_data.zone_root_depths[self._runtime_key] = self._value
        self.async_write_ha_state()
        await _async_request_plan_refresh(self._entry)


class BhyveZoneSoilWHCNumber(_BhyveZoneAgronomyNumber):
    """Persisted soil water-holding capacity for a single zone."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:water-percent"
    _attr_mode = "box"
    _attr_native_min_value = MIN_ZONE_SOIL_WHC_IN_PER_IN
    _attr_native_max_value = MAX_ZONE_SOIL_WHC_IN_PER_IN
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = "in/in"

    def __init__(self, entry: BhyveSprinklersConfigEntry, device_id: str, zone_number: int) -> None:
        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"{device_id}_{zone_number}_soil_whc"
        self._value = self._default_profile_values()["soil_whc_in_per_in"]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        stored_value = self._entry.runtime_data.zone_soil_whc.get(self._runtime_key)
        if stored_value is not None:
            self._value = float(stored_value)
        elif last_number_data is not None and last_number_data.native_value is not None:
            self._value = float(last_number_data.native_value)
        else:
            self._value = self._default_profile_values()["soil_whc_in_per_in"]
        self._entry.runtime_data.zone_soil_whc[self._runtime_key] = round(float(self._value), 3)

    @property
    def name(self) -> str | None:
        zone = self.zone
        if zone is None:
            return f"Zone {self._zone_number} soil WHC"
        return f"{zone.name} soil WHC"

    @property
    def native_value(self) -> float:
        return float(self._entry.runtime_data.zone_soil_whc.get(self._runtime_key, self._value))

    async def async_set_native_value(self, value: float) -> None:
        self._value = round(float(max(MIN_ZONE_SOIL_WHC_IN_PER_IN, min(MAX_ZONE_SOIL_WHC_IN_PER_IN, float(value)))), 3)
        self._entry.runtime_data.zone_soil_whc[self._runtime_key] = self._value
        self.async_write_ha_state()
        await _async_request_plan_refresh(self._entry)


class BhyveZoneMADNumber(_BhyveZoneAgronomyNumber):
    """Persisted management allowable depletion for a single zone."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:chart-timeline-variant"
    _attr_mode = "box"
    _attr_native_min_value = MIN_ZONE_MAD
    _attr_native_max_value = MAX_ZONE_MAD
    _attr_native_step = 0.01

    def __init__(self, entry: BhyveSprinklersConfigEntry, device_id: str, zone_number: int) -> None:
        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"{device_id}_{zone_number}_mad"
        self._value = self._default_profile_values()["mad"]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        stored_value = self._entry.runtime_data.zone_mad_values.get(self._runtime_key)
        if stored_value is not None:
            self._value = float(stored_value)
        elif last_number_data is not None and last_number_data.native_value is not None:
            self._value = float(last_number_data.native_value)
        else:
            self._value = self._default_profile_values()["mad"]
        self._entry.runtime_data.zone_mad_values[self._runtime_key] = round(float(self._value), 3)

    @property
    def name(self) -> str | None:
        zone = self.zone
        if zone is None:
            return f"Zone {self._zone_number} MAD"
        return f"{zone.name} MAD"

    @property
    def native_value(self) -> float:
        return float(self._entry.runtime_data.zone_mad_values.get(self._runtime_key, self._value))

    async def async_set_native_value(self, value: float) -> None:
        self._value = round(float(max(MIN_ZONE_MAD, min(MAX_ZONE_MAD, float(value)))), 3)
        self._entry.runtime_data.zone_mad_values[self._runtime_key] = self._value
        self.async_write_ha_state()
        await _async_request_plan_refresh(self._entry)


class BhyveZoneKCNumber(_BhyveZoneAgronomyNumber):
    """Persisted crop coefficient for a single zone."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:leaf"
    _attr_mode = "box"
    _attr_native_min_value = MIN_ZONE_KC
    _attr_native_max_value = MAX_ZONE_KC
    _attr_native_step = 0.01

    def __init__(self, entry: BhyveSprinklersConfigEntry, device_id: str, zone_number: int) -> None:
        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"{device_id}_{zone_number}_kc"
        self._value = self._default_profile_values()["kc"]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        stored_value = self._entry.runtime_data.zone_kc_values.get(self._runtime_key)
        if stored_value is not None:
            self._value = float(stored_value)
        elif last_number_data is not None and last_number_data.native_value is not None:
            self._value = float(last_number_data.native_value)
        else:
            self._value = self._default_profile_values()["kc"]
        self._entry.runtime_data.zone_kc_values[self._runtime_key] = round(float(self._value), 3)

    @property
    def name(self) -> str | None:
        zone = self.zone
        if zone is None:
            return f"Zone {self._zone_number} kc"
        return f"{zone.name} kc"

    @property
    def native_value(self) -> float:
        return float(self._entry.runtime_data.zone_kc_values.get(self._runtime_key, self._value))

    async def async_set_native_value(self, value: float) -> None:
        self._value = round(float(max(MIN_ZONE_KC, min(MAX_ZONE_KC, float(value)))), 3)
        self._entry.runtime_data.zone_kc_values[self._runtime_key] = self._value
        self.async_write_ha_state()
        await _async_request_plan_refresh(self._entry)


class BhyveZoneTriggerBufferNumber(_BhyveZoneAgronomyNumber):
    """Persisted trigger deadband for a single zone."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:ray-start-end"
    _attr_mode = "box"
    _attr_native_min_value = MIN_ZONE_TRIGGER_BUFFER_INCHES
    _attr_native_max_value = MAX_ZONE_TRIGGER_BUFFER_INCHES
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = UnitOfLength.INCHES

    def __init__(self, entry: BhyveSprinklersConfigEntry, device_id: str, zone_number: int) -> None:
        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"{device_id}_{zone_number}_trigger_buffer"
        self._value = float(DEFAULT_ZONE_TRIGGER_BUFFER_INCHES)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        stored_value = self._entry.runtime_data.zone_trigger_buffers.get(self._runtime_key)
        if stored_value is not None:
            self._value = float(stored_value)
        elif last_number_data is not None and last_number_data.native_value is not None:
            self._value = float(last_number_data.native_value)
        else:
            self._value = float(DEFAULT_ZONE_TRIGGER_BUFFER_INCHES)
        self._entry.runtime_data.zone_trigger_buffers[self._runtime_key] = round(float(self._value), 3)

    @property
    def name(self) -> str | None:
        zone = self.zone
        if zone is None:
            return f"Zone {self._zone_number} trigger buffer"
        return f"{zone.name} trigger buffer"

    @property
    def native_value(self) -> float:
        return float(self._entry.runtime_data.zone_trigger_buffers.get(self._runtime_key, self._value))

    async def async_set_native_value(self, value: float) -> None:
        self._value = round(float(max(MIN_ZONE_TRIGGER_BUFFER_INCHES, min(MAX_ZONE_TRIGGER_BUFFER_INCHES, float(value)))), 3)
        self._entry.runtime_data.zone_trigger_buffers[self._runtime_key] = self._value
        self.async_write_ha_state()
        await _async_request_plan_refresh(self._entry)


class BhyveZoneWateringCoefficientNumber(
    BhyveZoneCoordinatorEntity,
    RestoreNumber,
    NumberEntity,
):
    """Persisted user tuning coefficient for a single zone's water demand."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:tune-variant"
    _attr_mode = "box"
    _attr_native_min_value = MIN_ZONE_WATERING_COEFFICIENT
    _attr_native_max_value = MAX_ZONE_WATERING_COEFFICIENT
    _attr_native_step = 0.1

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the zone watering coefficient number."""

        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"{device_id}_{zone_number}_watering_coefficient"
        self._value = float(DEFAULT_ZONE_WATERING_COEFFICIENT)

    async def async_added_to_hass(self) -> None:
        """Restore the previous per-zone coefficient if one exists."""

        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        restored = False
        if last_number_data is not None and last_number_data.native_value is not None:
            self._value = round(
                max(
                    MIN_ZONE_WATERING_COEFFICIENT,
                    min(MAX_ZONE_WATERING_COEFFICIENT, float(last_number_data.native_value)),
                ),
                1,
            )
            restored = True
        self._store_runtime_value()
        if restored and self._entry.runtime_data.plan_coordinator is not None:
            self.hass.async_create_task(_async_request_plan_refresh(self._entry))

    @property
    def name(self) -> str | None:
        """Return the entity name."""

        zone = self.zone
        if zone is None:
            return f"Zone {self._zone_number} watering coefficient"
        return f"{zone.name} watering coefficient"

    @property
    def native_value(self) -> float:
        """Return the stored zone watering coefficient."""

        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Set the per-zone demand multiplier used by the planner."""

        self._value = round(
            max(
                MIN_ZONE_WATERING_COEFFICIENT,
                min(MAX_ZONE_WATERING_COEFFICIENT, float(value)),
            ),
            1,
        )
        self._store_runtime_value()
        self.async_write_ha_state()
        await _async_request_plan_refresh(self._entry)

    def _store_runtime_value(self) -> None:
        """Share the selected zone coefficient with the plan coordinator."""

        self._entry.runtime_data.zone_watering_coefficients[self._runtime_key] = float(
            self._value
        )

    @property
    def _runtime_key(self) -> str:
        """Return the shared runtime-data key for this zone."""

        return f"{self._device_id}:{self._zone_number}"


class BhyveZoneMaxWeeklyRunTimeNumber(
    BhyveZoneCoordinatorEntity,
    RestoreNumber,
    NumberEntity,
):
    """Persisted maximum weekly runtime for a zone."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:calendar-clock"
    _attr_mode = "box"
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_WEEKLY_RUN_TIME
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the zone max-weekly-runtime number."""

        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"{device_id}_{zone_number}_max_weekly_run_time"
        self._value = float(DEFAULT_MAX_WEEKLY_RUN_TIME)

    async def async_added_to_hass(self) -> None:
        """Restore the previous weekly runtime cap if one exists."""

        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        if last_number_data is not None and last_number_data.native_value is not None:
            self._value = float(last_number_data.native_value)
        self._entry.runtime_data.max_weekly_run_times[self._runtime_key] = int(self._value)

    @property
    def name(self) -> str | None:
        """Return the entity name."""

        zone = self.zone
        if zone is None:
            return f"Zone {self._zone_number} max weekly runtime"
        return f"{zone.name} max weekly runtime"

    @property
    def native_value(self) -> float:
        """Return the stored maximum weekly runtime in minutes."""

        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Set the stored maximum weekly runtime."""

        self._value = float(max(0, min(MAX_WEEKLY_RUN_TIME, round(value))))
        self._entry.runtime_data.max_weekly_run_times[self._runtime_key] = int(self._value)
        self.async_write_ha_state()
        await _async_request_plan_refresh(self._entry)

    @property
    def _runtime_key(self) -> str:
        """Return the shared runtime-data key for this zone."""

        return f"{self._device_id}:{self._zone_number}"


async def _async_request_plan_refresh(entry: BhyveSprinklersConfigEntry) -> None:
    """Refresh the planner when a persisted scheduling input changes."""

    plan_coordinator = entry.runtime_data.plan_coordinator
    if plan_coordinator is None:
        return
    await plan_coordinator.async_request_refresh()
