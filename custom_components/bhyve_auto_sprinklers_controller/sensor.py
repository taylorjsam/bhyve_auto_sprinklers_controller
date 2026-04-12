"""Diagnostic sensors for the B-hyve Auto Sprinklers Controller integration."""

from __future__ import annotations

import math
from datetime import datetime, time as dt_time, timedelta, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfLength, UnitOfTime
from homeassistant.core import State, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    AUTOMATIC_WINDOW_PREFERENCE_MORNING,
    CONF_DAILY_RAIN_ENTITY_ID,
    CONF_ET_ENTITY_ID,
    CONF_FORECAST_WEATHER_ENTITY_ID,
    CONF_HUMIDITY_ENTITY_ID,
    CONF_IRRADIANCE_ENTITY_ID,
    CONF_TEMPERATURE_ENTITY_ID,
    CONF_UV_INDEX_ENTITY_ID,
    CONF_WIND_GUST_ENTITY_ID,
    CONF_WIND_SPEED_ENTITY_ID,
    DEFAULT_ZONE_TRIGGER_BUFFER_INCHES,
    DOMAIN,
    normalize_zone_watering_profile,
    ZONE_AGRONOMY_DEFAULTS,
    ZONE_WATERING_PROFILE_ANNUAL_FLOWERS,
    ZONE_WATERING_PROFILE_DEFAULT,
    ZONE_WATERING_PROFILE_DISABLED,
    ZONE_WATERING_PROFILE_DROUGHT_TOLERANT,
    ZONE_WATERING_PROFILE_NATIVE_XERISCAPE,
    ZONE_WATERING_PROFILE_TREES_SHRUBS,
    ZONE_WATERING_PROFILE_VEGETABLE_GARDEN,
)
from .entity import (
    BhyveControllerCoordinatorEntity,
    BhyveControllerPlanCoordinatorEntity,
    BhyveZoneCoordinatorEntity,
    BhyveZonePlanCoordinatorEntity,
)
from .models import (
    BhyveIrrigationPlanSnapshot,
    BhyveIrrigationSnapshot,
    BhyveSprinklerControllerSnapshot,
    BhyveSprinklerZone,
    BhyveSprinklersConfigEntry,
    merged_zone_recent_events,
)
from .planner import compute_next_trigger_horizon, project_et_draw


async def async_setup_entry(
    hass,
    entry: BhyveSprinklersConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up B-hyve Auto Sprinklers Controller sensors from a config entry."""

    del hass
    entities: list[SensorEntity] = [
        BhyveConnectedDevicesSensor(entry),
        BhyveImportedWeatherInputSensor(
            entry,
            CONF_DAILY_RAIN_ENTITY_ID,
            "Daily rain",
            "mdi:weather-rainy",
        ),
        BhyveEffectiveRain24hSensor(entry),
        BhyveComputedWeeklyRainSensor(entry),
        BhyveImportedWeatherInputSensor(
            entry,
            CONF_UV_INDEX_ENTITY_ID,
            "Current UV index",
            "mdi:white-balance-sunny",
        ),
        BhyveImportedWeatherInputSensor(
            entry,
            CONF_HUMIDITY_ENTITY_ID,
            "Current humidity",
            "mdi:water-percent",
        ),
        BhyveImportedWeatherInputSensor(
            entry,
            CONF_IRRADIANCE_ENTITY_ID,
            "Solar radiation",
            "mdi:weather-sunny-alert",
        ),
        BhyveImportedWeatherInputSensor(
            entry,
            CONF_TEMPERATURE_ENTITY_ID,
            "Current temperature",
            "mdi:thermometer",
        ),
        BhyveImportedWeatherInputSensor(
            entry,
            CONF_WIND_SPEED_ENTITY_ID,
            "Current wind speed",
            "mdi:weather-windy",
        ),
        BhyveImportedWeatherInputSensor(
            entry,
            CONF_WIND_GUST_ENTITY_ID,
            "Current wind gust",
            "mdi:weather-windy-variant",
        ),
        BhyveComputedHourlyETSensor(entry),
        BhyveForecastRainSensor(entry),
    ]

    controllers = entry.runtime_data.coordinator.data.controllers
    if controllers:
        for controller in controllers:
            entities.append(BhyveControllerPlanDecisionSensor(entry, controller.device_id))
            entities.append(BhyveControllerAverageZoneDeficitSensor(entry, controller.device_id))
            entities.append(BhyveControllerRollingDeficitSensor(entry, controller.device_id))
            entities.append(BhyveControllerLastWateringSensor(entry, controller.device_id))
            entities.append(BhyveControllerNextCycleSensor(entry, controller.device_id))
            entities.append(
                BhyveControllerSuggestedWindowSensor(
                    entry,
                    controller.device_id,
                    key="suggested_start",
                    entity_name="Suggested watering start",
                    icon="mdi:clock-start",
                )
            )
            entities.append(
                BhyveControllerSuggestedWindowSensor(
                    entry,
                    controller.device_id,
                    key="suggested_end",
                    entity_name="Suggested watering end",
                    icon="mdi:clock-end",
                )
            )
            entities.append(
                BhyveControllerSuggestedWindowSensor(
                    entry,
                    controller.device_id,
                    key="effective_start",
                    entity_name="Active watering start",
                    icon="mdi:clock-start",
                )
            )
            entities.append(
                BhyveControllerSuggestedWindowSensor(
                    entry,
                    controller.device_id,
                    key="effective_end",
                    entity_name="Active watering end",
                    icon="mdi:clock-end",
                )
            )
            for zone in controller.zones:
                entities.append(
                    BhyveZoneSettingsSensor(
                        entry,
                        controller.device_id,
                        zone.zone_number,
                    )
                )
                entities.append(
                    BhyveZoneRecommendedRuntimeSensor(
                        entry,
                        controller.device_id,
                        zone.zone_number,
                    )
                )
                entities.append(
                    BhyveZoneOverviewRuntimeSensor(
                        entry,
                        controller.device_id,
                        zone.zone_number,
                    )
                )
                entities.append(
                    BhyveZoneWeeklyRuntimeSensor(
                        entry,
                        controller.device_id,
                        zone.zone_number,
                    )
                )
                entities.append(
                    BhyveZoneWeeklyCapStatusSensor(
                        entry,
                        controller.device_id,
                        zone.zone_number,
                    )
                )
                entities.append(
                    BhyveZoneDeficitSensor(
                        entry,
                        controller.device_id,
                        zone.zone_number,
                    )
                )
                entities.append(
                    BhyveZoneCapacitySensor(
                        entry,
                        controller.device_id,
                        zone.zone_number,
                    )
                )
        entities.extend(
            BhyveSprinklerControllerStatusSensor(entry, controller)
            for controller in controllers
        )
    else:
        entities.append(BhyveFallbackSprinklerStatusSensor(entry))

    async_add_entities(entities)


class BhyveConnectedDevicesSensor(
    CoordinatorEntity[BhyveIrrigationSnapshot],
    SensorEntity,
):
    """Sensor that exposes how many sprinkler controllers were discovered."""

    _attr_has_entity_name = True
    _attr_name = "Sprinkler controllers"
    _attr_icon = "mdi:sprinkler-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: BhyveSprinklersConfigEntry) -> None:
        """Initialize the sensor."""

        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_connected_devices"

    @property
    def native_value(self) -> int:
        """Return the number of sprinkler controllers visible to the integration."""

        return len(self.coordinator.data.controllers)

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        """Return helpful sprinkler-specific discovery counts."""

        return {
            "sprinkler_controller_count": len(self.coordinator.data.controllers),
            "coordinator_snapshot_count": self.coordinator.data.device_count,
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Create a diagnostic device entry for the account."""

        return _account_device_info(self._entry)


class BhyveImportedWeatherInputSensor(SensorEntity):
    """Mirrors a selected Home Assistant entity into this integration."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        option_key: str,
        entity_name: str,
        icon: str,
    ) -> None:
        """Initialize a mirrored weather-input sensor."""

        self._entry = entry
        self._option_key = option_key
        self._attr_name = entity_name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{option_key}"

    async def async_added_to_hass(self) -> None:
        """Subscribe to updates from the selected source entity."""

        source_entity_id = self._source_entity_id
        if source_entity_id is None:
            return

        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [source_entity_id],
                self._async_handle_source_state_change,
            )
        )

    @callback
    def _async_handle_source_state_change(self, event) -> None:
        """Write new state when the selected source entity changes."""

        del event
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | str | None:
        """Return the current value from the selected source entity."""

        if self._source_entity_id is None:
            return "not_configured"

        source_state = self._source_state
        if source_state is None:
            return "source_missing"
        if source_state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            return source_state.state

        try:
            return float(source_state.state)
        except ValueError:
            return source_state.state

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the source entity's unit when one exists."""

        source_state = self._source_state
        if source_state is None:
            return None
        unit = source_state.attributes.get("unit_of_measurement")
        return str(unit) if unit is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose source metadata for the mirrored input."""

        attributes = {
            "input_key": self._option_key,
            "configure_via": "B-hyve Account device > Configuration",
        }
        if self._source_entity_id is not None:
            attributes["source_entity_id"] = self._source_entity_id
        else:
            attributes["configuration_status"] = "not_configured"

        source_state = self._source_state
        if source_state is not None:
            friendly_name = source_state.attributes.get("friendly_name")
            if friendly_name is not None:
                attributes["source_friendly_name"] = str(friendly_name)
            source_unit = source_state.attributes.get("unit_of_measurement")
            if source_unit is not None:
                attributes["source_unit_of_measurement"] = str(source_unit)

        return attributes

    @property
    def device_info(self) -> DeviceInfo:
        """Attach mirrored input sensors to the account device."""

        return _account_device_info(self._entry)

    @property
    def _source_entity_id(self) -> str | None:
        """Return the configured source entity id for this input."""

        value = self._entry.options.get(self._option_key)
        if not value:
            return None
        return str(value)

    @property
    def _source_state(self) -> State | None:
        """Return the current Home Assistant state for the selected source."""

        if self._source_entity_id is None:
            return None
        return self.hass.states.get(self._source_entity_id)


class BhyveComputedHourlyETSensor(
    CoordinatorEntity[BhyveIrrigationPlanSnapshot],
    SensorEntity,
):
    """Expose the planner's computed hourly ET value."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Hourly ET"
    _attr_icon = "mdi:chart-bell-curve-cumulative"
    _attr_native_unit_of_measurement = "in/hr"

    def __init__(self, entry: BhyveSprinklersConfigEntry) -> None:
        """Initialize the computed ET sensor."""

        super().__init__(entry.runtime_data.plan_coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{CONF_ET_ENTITY_ID}"

    @property
    def native_value(self) -> float | str | None:
        """Return the computed hourly ET used by the planner."""

        controllers = self.coordinator.data.controllers
        if not controllers:
            return None
        if controllers[0].et_source == "solar_radiation_missing":
            return "not_configured"
        if controllers[0].et_source == "solar_radiation_unavailable":
            return "source_unavailable"
        return controllers[0].hourly_et_inches

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose how the ET value is being derived."""

        controllers = self.coordinator.data.controllers
        attributes: dict[str, object] = {
            "source": "computed_from_weather_inputs",
            "inputs": ["temperature", "humidity", "solar_radiation", "wind_speed"],
            "description": (
                "Hourly reference ET computed from the configured local weather "
                "inputs and used by the planner for bucket depletion."
            ),
        }
        solar_radiation_entity_id = self._entry.options.get(CONF_IRRADIANCE_ENTITY_ID)
        if solar_radiation_entity_id:
            attributes["solar_radiation_source_entity_id"] = str(solar_radiation_entity_id)
        if controllers:
            attributes["planner_et_source"] = controllers[0].et_source
        return attributes

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to the account device."""

        return _account_device_info(self._entry)


class BhyveComputedWeeklyRainSensor(
    CoordinatorEntity[BhyveIrrigationPlanSnapshot],
    SensorEntity,
):
    """Sensor reporting the last 7 days of raw rainfall from the water balance ledger."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Weekly rain (7-day)"
    _attr_icon = "mdi:weather-pouring"
    _attr_native_unit_of_measurement = UnitOfLength.INCHES

    def __init__(self, entry: BhyveSprinklersConfigEntry) -> None:
        """Initialize the computed weekly rain sensor."""

        super().__init__(entry.runtime_data.plan_coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_weekly_rain_computed"

    @property
    def native_value(self) -> float | str | None:
        """Return the rolling 7-day rain total from the water balance ledger."""

        controllers = self.coordinator.data.controllers
        if not controllers:
            return None
        return controllers[0].raw_rain_7d_inches

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose how the value is calculated."""

        return {
            "source": "water_balance_ledger",
            "window": "last_7_days",
            "description": "Sum of daily raw rainfall recorded over the past 7 days.",
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to the account device."""

        return _account_device_info(self._entry)


class BhyveEffectiveRain24hSensor(
    CoordinatorEntity[BhyveIrrigationPlanSnapshot],
    SensorEntity,
):
    """Expose the effective 24-hour rain credit used by the planner."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Effective rain (24h)"
    _attr_icon = "mdi:weather-pouring"
    _attr_native_unit_of_measurement = UnitOfLength.INCHES

    def __init__(self, entry: BhyveSprinklersConfigEntry) -> None:
        """Initialize the effective-rain sensor."""

        super().__init__(entry.runtime_data.plan_coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_effective_rain_24h"

    @property
    def native_value(self) -> float | str | None:
        """Return the effective rain credit used by the planner today."""

        controllers = self.coordinator.data.controllers
        if not controllers:
            return None
        return controllers[0].effective_rain_24h_inches

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the raw daily rain alongside the effective rain credit."""

        attributes: dict[str, object] = {
            "source": "planner_effective_rain_credit",
            "window": "current_day",
            "description": (
                "Planner-effective rain credit after discounting likely runoff "
                "and fast evaporation."
            ),
            "formula": (
                "Any rain above 0.0 in receives credit. Small rain below 0.25 in "
                "credits at 80%, then the planner uses a continuous runoff curve "
                "through these anchor points instead of hard jumps: "
                "0.25 in -> 0.20 in, 0.50 in -> 0.45 in, 0.75 in -> 0.50 in, "
                "1.50+ in -> 0.60 in. That base credit is then adjusted slightly "
                "by how many hours the rain actually accumulated across the day, "
                "so a slower soak credits more than a quick burst of the same total."
            ),
            "configure_via": "B-hyve Account device > Configuration",
        }
        controllers = self.coordinator.data.controllers
        if controllers:
            controller_plan = controllers[0]
            attributes["rain_active_hours_24h"] = controller_plan.rain_active_hours_24h
            attributes["average_rain_rate_inches_per_hour"] = (
                controller_plan.average_rain_rate_inches_per_hour
            )
        raw_rain_entity_id = self._entry.options.get(CONF_DAILY_RAIN_ENTITY_ID)
        if raw_rain_entity_id:
            attributes["raw_rain_source_entity_id"] = str(raw_rain_entity_id)
            raw_state = self.hass.states.get(str(raw_rain_entity_id))
            if raw_state is not None:
                attributes["raw_rain_24h_inches"] = raw_state.state
                friendly_name = raw_state.attributes.get("friendly_name")
                if friendly_name is not None:
                    attributes["raw_rain_source_friendly_name"] = str(friendly_name)
        return attributes

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to the account device."""

        return _account_device_info(self._entry)


class BhyveForecastRainSensor(
    CoordinatorEntity[BhyveIrrigationPlanSnapshot],
    SensorEntity,
):
    """Expose the next-24-hour forecast rain total used by the planner."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Forecast rain next 24h"
    _attr_icon = "mdi:weather-rainy"
    def __init__(self, entry: BhyveSprinklersConfigEntry) -> None:
        """Initialize the forecast rain sensor."""

        super().__init__(entry.runtime_data.plan_coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_forecast_rain_next_24h"

    @property
    def native_value(self) -> float | str | None:
        """Return the forecast rain total used by the planner."""

        if self.coordinator.data.forecast_source is None and self._has_configured_forecast_source:
            return "forecast_unavailable"
        if self.coordinator.data.forecast_source is None:
            return "not_configured"
        if self.coordinator.data.forecast_rain_amount_inches is None:
            return "forecast_unavailable"
        return self.coordinator.data.forecast_rain_amount_inches

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return inches only when a numeric forecast amount is available."""

        if isinstance(self.coordinator.data.forecast_rain_amount_inches, (int, float)):
            return UnitOfLength.INCHES
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose how the forecast was sourced."""

        attributes = {"configure_via": "B-hyve Account device > Configuration"}
        if self.coordinator.data.forecast_source is not None:
            attributes["forecast_source"] = self.coordinator.data.forecast_source
            if self.coordinator.data.forecast_rain_amount_inches is None:
                attributes["status"] = "forecast_unavailable"
            else:
                attributes["status"] = "configured"
        elif self._has_configured_forecast_source:
            attributes["status"] = "forecast_unavailable"
        else:
            attributes["status"] = "not_configured"
        return attributes

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the sensor to the account device."""

        return _account_device_info(self._entry)

    @property
    def _has_configured_forecast_source(self) -> bool:
        """Return True when a forecast weather entity is configured."""

        return bool(self._entry.options.get(CONF_FORECAST_WEATHER_ENTITY_ID))


class BhyveControllerPlanDecisionSensor(BhyveControllerPlanCoordinatorEntity, SensorEntity):
    """Expose the current planner decision for a controller."""

    _attr_has_entity_name = True
    _attr_name = "Irrigation decision"
    _attr_icon = "mdi:calendar-check"

    def __init__(self, entry: BhyveSprinklersConfigEntry, device_id: str) -> None:
        """Initialize the controller decision sensor."""

        super().__init__(entry, device_id)
        self._attr_unique_id = f"{device_id}_irrigation_decision"

    @property
    def native_value(self) -> str:
        """Return the current planner decision."""

        controller_plan = self.controller_plan
        if controller_plan is None:
            return "unavailable"
        return controller_plan.decision

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the details that drove the planner decision."""

        controller_plan = self.controller_plan
        if controller_plan is None:
            return {}
        average_zone_deficit = _average_zone_deficit(controller_plan)
        highest_zone_plan = _highest_zone_plan_for_summary(controller_plan)
        return {
            "reason": controller_plan.reason,
            "rolling_deficit_inches": controller_plan.deficit_inches,
            "average_zone_deficit_inches": average_zone_deficit,
            "highest_zone_deficit_inches": (
                float(highest_zone_plan.deficit_inches)
                if highest_zone_plan is not None
                else controller_plan.deficit_inches
            ),
            "deficit_basis": controller_plan.deficit_basis,
            "peak_deficit_zone_name": (
                highest_zone_plan.zone_name
                if highest_zone_plan is not None
                else controller_plan.peak_deficit_zone_name
            ),
            "effective_rain_24h_inches": controller_plan.effective_rain_24h_inches,
            "effective_rain_7d_inches": controller_plan.effective_rain_7d_inches,
            "et_today_inches": controller_plan.et_today_inches,
            "et_7d_inches": controller_plan.et_7d_inches,
            "temperature_f": controller_plan.temperature_f,
            "humidity_percent": controller_plan.humidity_percent,
            "wind_speed_mph": controller_plan.wind_speed_mph,
            "wind_gust_mph": controller_plan.wind_gust_mph,
            "min_watering_temperature_f": controller_plan.min_watering_temperature_f,
            "max_watering_wind_speed_mph": controller_plan.max_watering_wind_speed_mph,
            "effective_max_watering_wind_speed_mph": controller_plan.effective_max_watering_wind_speed_mph,
            "max_watering_gust_speed_mph": controller_plan.max_watering_gust_speed_mph,
            "wind_profile_mode": controller_plan.sprinkler_wind_profile,
            "weather_hold_active": controller_plan.weather_hold_active,
            "weather_stop_held_today": controller_plan.weather_stop_held_today,
            "irrigation_7d_inches": controller_plan.irrigation_7d_inches,
            "irrigation_7d_minutes": controller_plan.irrigation_7d_minutes,
            "weekly_target_inches": controller_plan.weekly_target_inches,
            "location_latitude": controller_plan.location_latitude,
            "location_longitude": controller_plan.location_longitude,
            "location_source": controller_plan.location_source,
            "forecast_rain_amount_inches": controller_plan.forecast_rain_amount_inches,
            "rain_delay_days": controller_plan.rain_delay_days,
            "dry_days_streak": controller_plan.dry_days_streak,
            "automatic_watering_enabled": self._entry.runtime_data.automatic_watering_enabled,
            "notifications_enabled": self._entry.runtime_data.notifications_enabled,
            "notification_target": self._entry.runtime_data.notification_service,
            "automatic_window_enabled": controller_plan.automatic_window_enabled,
            "automatic_window_preference": controller_plan.automatic_window_preference,
            "automatic_window_max_minutes": controller_plan.automatic_window_max_minutes,
            "suggested_start_time": controller_plan.suggested_start_time,
            "suggested_end_time": controller_plan.suggested_end_time,
            "effective_start_time": controller_plan.effective_start_time,
            "effective_end_time": controller_plan.effective_end_time,
            "available_window_minutes": controller_plan.available_window_minutes,
            "automatic_window_reason": controller_plan.automatic_window_reason,
            "current_weekday_name": controller_plan.current_weekday_name,
            "controller_day_restriction": controller_plan.controller_day_restriction,
            "allowed_days_per_week": controller_plan.allowed_days_per_week,
            "total_requested_runtime_minutes": controller_plan.total_requested_runtime_minutes,
            "total_recommended_runtime_minutes": controller_plan.total_recommended_runtime_minutes,
            "window_rotation_applied": controller_plan.window_rotation_applied,
            "allowed_now": controller_plan.allowed_now,
            "weather_source_status": controller_plan.weather_source_status,
            "last_evaluated": controller_plan.last_evaluated,
            "recommended_zone_count": sum(
                1
                for zone_plan in controller_plan.zone_plans
                if zone_plan.recommended_runtime_minutes > 0
            ),
        }


class BhyveControllerRollingDeficitSensor(BhyveControllerPlanCoordinatorEntity, SensorEntity):
    """Expose the average active-zone deficit for dashboard summaries."""

    _attr_has_entity_name = True
    _attr_name = "Average deficit"
    _attr_icon = "mdi:waves-arrow-up"
    _attr_native_unit_of_measurement = UnitOfLength.INCHES
    _attr_suggested_display_precision = 2

    def __init__(self, entry: BhyveSprinklersConfigEntry, device_id: str) -> None:
        """Initialize the deficit sensor."""

        super().__init__(entry, device_id)
        self._attr_unique_id = f"{device_id}_rolling_deficit"

    @property
    def native_value(self) -> float | None:
        """Return the current average active-zone deficit in inches."""

        controller_plan = self.controller_plan
        if controller_plan is None:
            return None
        average_zone_deficit = _average_zone_deficit(controller_plan)
        if average_zone_deficit is not None:
            return average_zone_deficit
        return controller_plan.deficit_inches

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose supporting water-balance context."""

        controller_plan = self.controller_plan
        if controller_plan is None:
            return {}
        average_zone_deficit = _average_zone_deficit(controller_plan)
        average_zone_raw_deficit = _average_zone_raw_deficit(controller_plan)
        highest_zone_plan = _highest_zone_plan_for_summary(controller_plan)
        return {
            "average_zone_deficit_inches": average_zone_deficit,
            "raw_average_zone_deficit_inches": average_zone_raw_deficit,
            "highest_zone_deficit_inches": (
                float(highest_zone_plan.deficit_inches)
                if highest_zone_plan is not None
                else controller_plan.deficit_inches
            ),
            "raw_highest_zone_deficit_inches": (
                float(highest_zone_plan.raw_deficit_inches)
                if highest_zone_plan is not None
                else controller_plan.raw_deficit_inches
            ),
            "raw_rolling_deficit_inches": average_zone_raw_deficit,
            "deficit_basis": "average_active_zone_deficit",
            "peak_deficit_zone_name": (
                highest_zone_plan.zone_name
                if highest_zone_plan is not None
                else controller_plan.peak_deficit_zone_name
            ),
            "effective_rain_24h_inches": controller_plan.effective_rain_24h_inches,
            "rain_active_hours_24h": controller_plan.rain_active_hours_24h,
            "average_rain_rate_inches_per_hour": controller_plan.average_rain_rate_inches_per_hour,
            "effective_rain_7d_inches": controller_plan.effective_rain_7d_inches,
            "et_today_inches": controller_plan.et_today_inches,
            "et_7d_inches": controller_plan.et_7d_inches,
            "temperature_f": controller_plan.temperature_f,
            "humidity_percent": controller_plan.humidity_percent,
            "wind_speed_mph": controller_plan.wind_speed_mph,
            "wind_gust_mph": controller_plan.wind_gust_mph,
            "min_watering_temperature_f": controller_plan.min_watering_temperature_f,
            "max_watering_wind_speed_mph": controller_plan.max_watering_wind_speed_mph,
            "effective_max_watering_wind_speed_mph": controller_plan.effective_max_watering_wind_speed_mph,
            "max_watering_gust_speed_mph": controller_plan.max_watering_gust_speed_mph,
            "wind_profile_mode": controller_plan.sprinkler_wind_profile,
            "weather_hold_active": controller_plan.weather_hold_active,
            "weather_stop_held_today": controller_plan.weather_stop_held_today,
            "irrigation_7d_inches": controller_plan.irrigation_7d_inches,
            "irrigation_7d_minutes": controller_plan.irrigation_7d_minutes,
            "weekly_target_inches": controller_plan.weekly_target_inches,
            "et_multiplier": controller_plan.et_multiplier,
            "location_latitude": controller_plan.location_latitude,
            "location_longitude": controller_plan.location_longitude,
            "location_source": controller_plan.location_source,
            "decision": controller_plan.decision,
            "automatic_window_enabled": controller_plan.automatic_window_enabled,
            "automatic_window_preference": controller_plan.automatic_window_preference,
            "automatic_window_max_minutes": controller_plan.automatic_window_max_minutes,
            "suggested_start_time": controller_plan.suggested_start_time,
            "suggested_end_time": controller_plan.suggested_end_time,
            "effective_start_time": controller_plan.effective_start_time,
            "effective_end_time": controller_plan.effective_end_time,
            "available_window_minutes": controller_plan.available_window_minutes,
            "automatic_window_reason": controller_plan.automatic_window_reason,
            "current_weekday_name": controller_plan.current_weekday_name,
            "controller_day_restriction": controller_plan.controller_day_restriction,
            "allowed_days_per_week": controller_plan.allowed_days_per_week,
            "total_requested_runtime_minutes": controller_plan.total_requested_runtime_minutes,
            "total_recommended_runtime_minutes": controller_plan.total_recommended_runtime_minutes,
            "window_rotation_applied": controller_plan.window_rotation_applied,
        }


class BhyveControllerAverageZoneDeficitSensor(
    BhyveControllerPlanCoordinatorEntity,
    SensorEntity,
):
    """Expose a dedicated controller average-deficit entity for dashboards."""

    _attr_has_entity_name = True
    _attr_name = "Average zone deficit summary"
    _attr_icon = "mdi:waves-arrow-up"
    _attr_native_unit_of_measurement = UnitOfLength.INCHES
    _attr_suggested_display_precision = 2

    def __init__(self, entry: BhyveSprinklersConfigEntry, device_id: str) -> None:
        """Initialize the average zone deficit sensor."""

        super().__init__(entry, device_id)
        self._attr_unique_id = f"{device_id}_average_zone_deficit_summary"

    @property
    def native_value(self) -> float | None:
        """Return the current average active-zone deficit in inches."""

        controller_plan = self.controller_plan
        if controller_plan is None:
            return None
        average_zone_deficit = _average_zone_deficit(controller_plan)
        if average_zone_deficit is not None:
            return average_zone_deficit
        return controller_plan.deficit_inches

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose supporting average-deficit context."""

        controller_plan = self.controller_plan
        if controller_plan is None:
            return {}
        average_zone_deficit = _average_zone_deficit(controller_plan)
        average_zone_raw_deficit = _average_zone_raw_deficit(controller_plan)
        highest_zone_plan = _highest_zone_plan_for_summary(controller_plan)
        return {
            "average_zone_deficit_inches": average_zone_deficit,
            "raw_average_zone_deficit_inches": average_zone_raw_deficit,
            "highest_zone_deficit_inches": (
                float(highest_zone_plan.deficit_inches)
                if highest_zone_plan is not None
                else controller_plan.deficit_inches
            ),
            "raw_highest_zone_deficit_inches": (
                float(highest_zone_plan.raw_deficit_inches)
                if highest_zone_plan is not None
                else controller_plan.raw_deficit_inches
            ),
            "deficit_basis": "average_active_zone_deficit",
            "peak_deficit_zone_name": (
                highest_zone_plan.zone_name
                if highest_zone_plan is not None
                else controller_plan.peak_deficit_zone_name
            ),
        }


class BhyveControllerLastWateringSensor(BhyveControllerCoordinatorEntity, SensorEntity):
    """Expose recent watering history for dashboard use."""

    _attr_has_entity_name = True
    _attr_name = "Last watering"
    _attr_icon = "mdi:history"

    def __init__(self, entry: BhyveSprinklersConfigEntry, device_id: str) -> None:
        """Initialize the controller history sensor."""

        super().__init__(entry, device_id)
        self._attr_unique_id = f"{device_id}_last_watering"

    @property
    def native_value(self) -> str:
        """Return the most recent controller watering event."""

        controller = self.controller
        if controller is None:
            return "unavailable"
        events = _controller_recent_events(controller)
        if not events:
            return "never"
        return str(events[0]["end_local"])

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose a few weeks of recent watering activity."""

        controller = self.controller
        if controller is None:
            return {}
        events = _controller_recent_events(controller)
        attributes: dict[str, object] = {
            "recent_events": events,
            "recent_event_count": len(events),
            "watering_minutes_last_21d": sum(
                int(event.get("duration_minutes", 0)) for event in events
            ),
        }
        if events:
            latest = events[0]
            attributes.update(
                {
                    "zone_name": latest.get("zone_name"),
                    "zone_number": latest.get("zone_number"),
                    "duration_minutes": latest.get("duration_minutes"),
                    "schedule_name": latest.get("schedule_name"),
                    "schedule_type": latest.get("schedule_type"),
                }
            )
        return attributes


class BhyveControllerNextCycleSensor(
    BhyveControllerPlanCoordinatorEntity,
    SensorEntity,
):
    """Expose the projected next watering cycle for dashboard use."""

    _attr_has_entity_name = True
    _attr_name = "Next watering cycle"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, entry: BhyveSprinklersConfigEntry, device_id: str) -> None:
        """Initialize the projected cycle sensor."""

        super().__init__(entry, device_id)
        self._attr_unique_id = f"{device_id}_next_watering_cycle"

    @property
    def native_value(self) -> str:
        """Return the projected next cycle start."""

        controller_plan = self.controller_plan
        if controller_plan is None:
            return "unavailable"
        projected = _projected_cycle(controller_plan)
        if projected["start_local"] is None:
            return str(projected["status"])
        return str(projected["start_local"])

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the projected cycle window and zone runtimes."""

        controller_plan = self.controller_plan
        if controller_plan is None:
            return {}
        projected = _projected_cycle(controller_plan)
        estimated_need = _estimated_next_need(self._entry, controller_plan, projected)
        average_zone_deficit = _average_zone_deficit(controller_plan)
        highest_zone_plan = _highest_zone_plan_for_summary(controller_plan)
        automatic_watering_enabled = self._entry.runtime_data.automatic_watering_enabled
        projected_status = str(projected["status"])
        return {
            "status": projected_status,
            "status_label": _projected_cycle_status_label(
                projected_status,
                automatic_watering_enabled=automatic_watering_enabled,
            ),
            "plan_label": _projected_cycle_status_label(
                projected_status,
                automatic_watering_enabled=automatic_watering_enabled,
            ),
            "reason": _projected_cycle_reason(
                projected_status,
                controller_plan.reason,
                automatic_watering_enabled=automatic_watering_enabled,
            ),
            "rolling_deficit_inches": controller_plan.deficit_inches,
            "raw_rolling_deficit_inches": controller_plan.raw_deficit_inches,
            "average_zone_deficit_inches": average_zone_deficit,
            "highest_zone_deficit_inches": (
                float(highest_zone_plan.deficit_inches)
                if highest_zone_plan is not None
                else controller_plan.deficit_inches
            ),
            "raw_highest_zone_deficit_inches": (
                float(highest_zone_plan.raw_deficit_inches)
                if highest_zone_plan is not None
                else controller_plan.raw_deficit_inches
            ),
            "deficit_basis": controller_plan.deficit_basis,
            "peak_deficit_zone_name": (
                highest_zone_plan.zone_name
                if highest_zone_plan is not None
                else controller_plan.peak_deficit_zone_name
            ),
            "projected_start_local": projected["start_local"],
            "projected_end_local": projected["end_local"],
            "projected_day_offset": projected["day_offset"],
            "estimated_next_need_start_local": estimated_need["start_local"],
            "estimated_next_need_end_local": estimated_need["end_local"],
            "estimated_next_need_day_offset": estimated_need["day_offset"],
            "estimated_daily_recovery_inches": estimated_need["daily_recovery_inches"],
            "decision": controller_plan.decision,
            "available_window_minutes": controller_plan.available_window_minutes,
            "total_requested_runtime_minutes": controller_plan.total_requested_runtime_minutes,
            "total_recommended_runtime_minutes": controller_plan.total_recommended_runtime_minutes,
            "window_rotation_applied": controller_plan.window_rotation_applied,
            "automatic_window_enabled": controller_plan.automatic_window_enabled,
            "automatic_window_max_minutes": controller_plan.automatic_window_max_minutes,
            "current_weekday_name": controller_plan.current_weekday_name,
            "controller_day_restriction": controller_plan.controller_day_restriction,
            "allowed_days_per_week": controller_plan.allowed_days_per_week,
            "projected_zone_runs": [
                {
                    "zone_name": zone_plan.zone_name,
                    "deficit_inches": zone_plan.deficit_inches,
                    "requested_runtime_minutes": zone_plan.requested_runtime_minutes,
                    "runtime_minutes": zone_plan.recommended_runtime_minutes,
                    "minimum_run_threshold_minutes": zone_plan.minimum_run_threshold_minutes,
                    "runtime_bank_minutes": zone_plan.runtime_bank_minutes,
                    "cycle_minutes": list(zone_plan.cycle_minutes),
                    "forced_by_skip_limit": zone_plan.forced_by_skip_limit,
                    "deferred_by_window_limit": zone_plan.deferred_by_window_limit,
                }
                for zone_plan in controller_plan.zone_plans
                if zone_plan.recommended_runtime_minutes > 0
            ],
        }


class BhyveControllerSuggestedWindowSensor(
    BhyveControllerPlanCoordinatorEntity,
    SensorEntity,
):
    """Expose the planner's suggested or active watering window times."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        *,
        key: str,
        entity_name: str,
        icon: str,
    ) -> None:
        """Initialize the watering-window sensor."""

        super().__init__(entry, device_id)
        self._key = key
        self._attr_name = entity_name
        self._attr_icon = icon
        self._attr_unique_id = f"{device_id}_{key}_watering_time"

    @property
    def native_value(self) -> str:
        """Return the planner time string."""

        controller_plan = self.controller_plan
        if controller_plan is None:
            return "unavailable"
        mapping = {
            "suggested_start": controller_plan.suggested_start_time,
            "suggested_end": controller_plan.suggested_end_time,
            "effective_start": controller_plan.effective_start_time,
            "effective_end": controller_plan.effective_end_time,
        }
        return mapping[self._key]

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose how the planner chose the window."""

        controller_plan = self.controller_plan
        if controller_plan is None:
            return {}
        return {
            "automatic_window_enabled": controller_plan.automatic_window_enabled,
            "automatic_window_preference": controller_plan.automatic_window_preference,
            "automatic_window_reason": controller_plan.automatic_window_reason,
            "total_recommended_runtime_minutes": controller_plan.total_recommended_runtime_minutes,
            "location_latitude": controller_plan.location_latitude,
            "location_longitude": controller_plan.location_longitude,
            "location_source": controller_plan.location_source,
            "decision": controller_plan.decision,
        }


class BhyveZoneRecommendedRuntimeSensor(BhyveZonePlanCoordinatorEntity, SensorEntity):
    """Expose the planner's recommended runtime for a zone."""

    _attr_has_entity_name = True
    _attr_name = "Recommended runtime"
    _attr_icon = "mdi:timer-play"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the zone runtime recommendation sensor."""

        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"{device_id}_{zone_number}_recommended_runtime"

    @property
    def native_value(self) -> int | None:
        """Return the recommended runtime in minutes."""

        zone_plan = self.zone_plan
        if zone_plan is None:
            return None
        return zone_plan.recommended_runtime_minutes

    @property
    def name(self) -> str | None:
        """Return the entity name."""

        zone_plan = self.zone_plan
        if zone_plan is None:
            return f"Zone {self._zone_number} recommended runtime"
        return f"{zone_plan.zone_name} recommended runtime"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the math behind the zone runtime recommendation."""

        zone_plan = self.zone_plan
        if zone_plan is None:
            return {}
        return {
            "deficit_inches": zone_plan.deficit_inches,
            "raw_deficit_inches": zone_plan.raw_deficit_inches,
            "application_rate_configured": zone_plan.application_rate_configured,
            "application_rate_inches_per_hour": zone_plan.application_rate_inches_per_hour,
            "requested_runtime_minutes": zone_plan.requested_runtime_minutes,
            "minimum_run_threshold_minutes": zone_plan.minimum_run_threshold_minutes,
            "effective_minimum_run_threshold_minutes": zone_plan.effective_minimum_run_threshold_minutes,
            "runtime_bank_minutes": zone_plan.runtime_bank_minutes,
            "cycle_minutes": list(zone_plan.cycle_minutes),
            "cycle_and_soak_required": len(zone_plan.cycle_minutes) > 1,
            "scale_factor": zone_plan.scale_factor,
            "weekly_target_inches": zone_plan.weekly_target_inches,
            "estimated_application_inches": zone_plan.estimated_application_inches,
            "recent_runtime_minutes_7d": zone_plan.recent_runtime_minutes_7d,
            "recent_irrigation_inches_7d": zone_plan.recent_irrigation_inches_7d,
            "remaining_weekly_runtime_minutes": zone_plan.remaining_weekly_runtime_minutes,
            "capped_by_session_limit": zone_plan.capped_by_session_limit,
            "capped_by_weekly_limit": zone_plan.capped_by_weekly_limit,
            "allowable_depletion_inches": zone_plan.allowable_depletion_inches,
            "crop_coefficient": zone_plan.crop_coefficient,
            "user_watering_coefficient": zone_plan.user_watering_coefficient,
            "zone_demand_multiplier": zone_plan.zone_demand_multiplier,
            "sprinkler_head_type": zone_plan.sprinkler_head_type,
            "effective_max_watering_wind_speed_mph": zone_plan.effective_max_watering_wind_speed_mph,
            "max_watering_gust_speed_mph": zone_plan.max_watering_gust_speed_mph,
            "weather_hold_active": zone_plan.weather_hold_active,
            "exposure_factor": zone_plan.exposure_factor,
            "seasonal_factor": zone_plan.seasonal_factor,
            "soil_storage_factor": zone_plan.soil_storage_factor,
            "storage_buffer_days": zone_plan.storage_buffer_days,
            "session_limit_minutes": zone_plan.session_limit_minutes,
            "watering_profile": zone_plan.watering_profile,
            "water_efficient_mode": zone_plan.water_efficient_mode,
            "trees_shrubs_mode": zone_plan.trees_shrubs_mode,
            "vegetable_garden_mode": zone_plan.vegetable_garden_mode,
            "banked_by_weather_hold": zone_plan.banked_by_weather_hold,
            "target_interval_days": zone_plan.target_interval_days,
            "days_since_last_watering": zone_plan.days_since_last_watering,
            "days_until_due": zone_plan.days_until_due,
            "forced_by_skip_limit": zone_plan.forced_by_skip_limit,
            "deferred_by_window_limit": zone_plan.deferred_by_window_limit,
            "reason": zone_plan.reason,
        }


class BhyveZoneOverviewRuntimeSensor(BhyveZonePlanCoordinatorEntity, SensorEntity):
    """Expose a live dashboard row for the overview zone summary."""

    _attr_has_entity_name = False
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the overview runtime summary sensor."""

        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"{device_id}_{zone_number}_overview_runtime"

    @property
    def native_value(self) -> int | None:
        """Return the live runtime shown on the right side of the overview row."""

        zone_plan = self.zone_plan
        if zone_plan is None:
            return None
        return zone_plan.recommended_runtime_minutes

    @property
    def name(self) -> str | None:
        """Return a compact live row label for the overview dashboard."""

        zone_plan = self.zone_plan
        if zone_plan is None:
            return f"Zone {self._zone_number} · loading"
        return (
            f"{_compact_zone_name(zone_plan.zone_name)} · "
            f"{_overview_profile_label(zone_plan.watering_profile)} · "
            f"{_overview_application_rate_label(zone_plan.application_rate_inches_per_hour)}"
        )

    @property
    def icon(self) -> str:
        """Return a profile-aware icon for the overview dashboard row."""

        zone_plan = self.zone_plan
        if zone_plan is None:
            return "mdi:sprinkler-variant"
        return _overview_profile_icon(zone_plan.watering_profile)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the live row inputs for debugging/custom dashboards."""

        zone_plan = self.zone_plan
        if zone_plan is None:
            return {}
        return {
            "zone_name": zone_plan.zone_name,
            "compact_zone_name": _compact_zone_name(zone_plan.zone_name),
            "watering_profile": zone_plan.watering_profile,
            "application_rate_inches_per_hour": zone_plan.application_rate_inches_per_hour,
            "application_rate_configured": zone_plan.application_rate_configured,
            "recommended_runtime_minutes": zone_plan.recommended_runtime_minutes,
        }


class BhyveZoneWeeklyRuntimeSensor(BhyveZonePlanCoordinatorEntity, SensorEntity):
    """Expose the recent 7-day runtime total for a zone."""

    _attr_has_entity_name = True
    _attr_name = "Runtime this week"
    _attr_icon = "mdi:calendar-week"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the zone weekly-runtime sensor."""

        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"{device_id}_{zone_number}_runtime_this_week"

    @property
    def native_value(self) -> int | None:
        """Return the recent 7-day runtime in minutes."""

        zone_plan = self.zone_plan
        if zone_plan is None:
            return None
        return zone_plan.recent_runtime_minutes_7d

    @property
    def name(self) -> str | None:
        """Return the entity name."""

        zone_plan = self.zone_plan
        if zone_plan is None:
            return f"Zone {self._zone_number} runtime this week"
        return f"{zone_plan.zone_name} runtime this week"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose supporting recent-irrigation context."""

        zone_plan = self.zone_plan
        if zone_plan is None:
            return {}
        return {
            "recent_runtime_minutes_7d": zone_plan.recent_runtime_minutes_7d,
            "recent_irrigation_inches_7d": zone_plan.recent_irrigation_inches_7d,
            "remaining_weekly_runtime_minutes": zone_plan.remaining_weekly_runtime_minutes,
            "weekly_target_inches": zone_plan.weekly_target_inches,
            "watering_profile": zone_plan.watering_profile,
            "reason": zone_plan.reason,
        }


class BhyveZoneWeeklyCapStatusSensor(BhyveZonePlanCoordinatorEntity, SensorEntity):
    """Expose whether a zone's weekly runtime cap is active."""

    _attr_has_entity_name = True
    _attr_name = "Weekly cap status"
    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the zone weekly-cap status sensor."""

        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"{device_id}_{zone_number}_weekly_cap_status"

    @property
    def native_value(self) -> str | None:
        """Return a human-friendly weekly-cap status."""

        zone_plan = self.zone_plan
        if zone_plan is None:
            return None

        runtime_key = f"{self._device_id}:{self._zone_number}"
        weekly_cap_minutes = int(
            self._entry.runtime_data.max_weekly_run_times.get(runtime_key, 0) or 0
        )
        if weekly_cap_minutes <= 0:
            return "Not used"
        return f"{weekly_cap_minutes} min"

    @property
    def name(self) -> str | None:
        """Return the entity name."""

        zone_plan = self.zone_plan
        if zone_plan is None:
            return f"Zone {self._zone_number} weekly cap status"
        return f"{zone_plan.zone_name} weekly cap status"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose whether the weekly cap is active and what remains."""

        zone_plan = self.zone_plan
        if zone_plan is None:
            return {}

        runtime_key = f"{self._device_id}:{self._zone_number}"
        weekly_cap_minutes = int(
            self._entry.runtime_data.max_weekly_run_times.get(runtime_key, 0) or 0
        )
        return {
            "weekly_cap_enabled": weekly_cap_minutes > 0,
            "weekly_cap_minutes": weekly_cap_minutes,
            "remaining_weekly_runtime_minutes": zone_plan.remaining_weekly_runtime_minutes,
            "recent_runtime_minutes_7d": zone_plan.recent_runtime_minutes_7d,
            "display_note": (
                "A weekly cap of 0 disables the cap."
                if weekly_cap_minutes <= 0
                else "Weekly cap is active."
            ),
        }


class BhyveZoneDeficitSensor(BhyveZonePlanCoordinatorEntity, SensorEntity):
    """Expose the rolling water deficit for a single zone."""

    _attr_has_entity_name = True
    _attr_name = "Zone deficit"
    _attr_icon = "mdi:waves-arrow-up"
    _attr_native_unit_of_measurement = UnitOfLength.INCHES
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the zone deficit sensor."""

        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"{device_id}_{zone_number}_zone_deficit"

    @property
    def native_value(self) -> float | None:
        """Return the zone-specific rolling deficit in inches."""

        zone_plan = self.zone_plan
        if zone_plan is None:
            return None
        return zone_plan.deficit_inches

    @property
    def name(self) -> str | None:
        """Return the entity name."""

        zone_plan = self.zone_plan
        if zone_plan is None:
            return f"Zone {self._zone_number} deficit"
        return f"{zone_plan.zone_name} deficit"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose supporting zone-specific water-balance context."""

        zone_plan = self.zone_plan
        if zone_plan is None:
            return {}
        return {
            "raw_deficit_inches": zone_plan.raw_deficit_inches,
            "capacity_inches": zone_plan.capacity_inches,
            "current_water_inches": zone_plan.current_water_inches,
            "trigger_buffer_inches": zone_plan.trigger_buffer_inches,
            "projected_et_draw_inches": zone_plan.projected_et_draw_inches,
            "projected_daylight_hours": zone_plan.projected_daylight_hours,
            "projected_remaining_inches": zone_plan.projected_remaining_inches,
            "zone_hourly_et_inches": zone_plan.zone_hourly_et_inches,
            "zone_daily_et_inches": zone_plan.zone_daily_et_inches,
            "trigger_active": zone_plan.trigger_active,
            "weekly_target_inches": zone_plan.weekly_target_inches,
            "application_rate_configured": zone_plan.application_rate_configured,
            "application_rate_inches_per_hour": zone_plan.application_rate_inches_per_hour,
            "recommended_runtime_minutes": zone_plan.recommended_runtime_minutes,
            "requested_runtime_minutes": zone_plan.requested_runtime_minutes,
            "runtime_bank_minutes": zone_plan.runtime_bank_minutes,
            "minimum_run_threshold_minutes": zone_plan.minimum_run_threshold_minutes,
            "effective_minimum_run_threshold_minutes": zone_plan.effective_minimum_run_threshold_minutes,
            "remaining_weekly_runtime_minutes": zone_plan.remaining_weekly_runtime_minutes,
            "recent_runtime_minutes_7d": zone_plan.recent_runtime_minutes_7d,
            "recent_irrigation_inches_7d": zone_plan.recent_irrigation_inches_7d,
            "crop_coefficient": zone_plan.crop_coefficient,
            "user_watering_coefficient": zone_plan.user_watering_coefficient,
            "zone_demand_multiplier": zone_plan.zone_demand_multiplier,
            "sprinkler_head_type": zone_plan.sprinkler_head_type,
            "effective_max_watering_wind_speed_mph": zone_plan.effective_max_watering_wind_speed_mph,
            "max_watering_gust_speed_mph": zone_plan.max_watering_gust_speed_mph,
            "weather_hold_active": zone_plan.weather_hold_active,
            "exposure_factor": zone_plan.exposure_factor,
            "seasonal_factor": zone_plan.seasonal_factor,
            "soil_storage_factor": zone_plan.soil_storage_factor,
            "storage_buffer_days": zone_plan.storage_buffer_days,
            "watering_profile": zone_plan.watering_profile,
            "weekday_name": zone_plan.weekday_name,
            "controller_day_restriction": zone_plan.controller_day_restriction,
            "zone_day_restriction": zone_plan.zone_day_restriction,
            "schedule_hold_active": zone_plan.schedule_hold_active,
            "allowed_days_per_week": zone_plan.allowed_days_per_week,
            "target_interval_days": zone_plan.target_interval_days,
            "days_since_last_watering": zone_plan.days_since_last_watering,
            "forced_by_skip_limit": zone_plan.forced_by_skip_limit,
            "banked_by_weather_hold": zone_plan.banked_by_weather_hold,
            "deferred_by_window_limit": zone_plan.deferred_by_window_limit,
            "reason": zone_plan.reason,
        }


class BhyveZoneCapacitySensor(BhyveZonePlanCoordinatorEntity, SensorEntity):
    """Expose the computed allowable-depletion bucket size for a zone."""

    _attr_has_entity_name = True
    _attr_name = "Capacity"
    _attr_icon = "mdi:cup-water"
    _attr_native_unit_of_measurement = UnitOfLength.INCHES
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the zone capacity sensor."""

        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"{device_id}_{zone_number}_capacity"

    @property
    def native_value(self) -> float | None:
        """Return the current computed zone capacity."""

        zone_plan = self.zone_plan
        if zone_plan is not None:
            return zone_plan.capacity_inches
        values = _resolved_zone_agronomy_values(
            self._entry,
            self._device_id,
            self._zone_number,
        )
        return values["capacity_inches"]

    @property
    def name(self) -> str | None:
        """Return the entity name."""

        zone_plan = self.zone_plan
        if zone_plan is None:
            return f"Zone {self._zone_number} capacity"
        return f"{zone_plan.zone_name} capacity"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the inputs that produce the derived zone capacity."""

        values = _resolved_zone_agronomy_values(
            self._entry,
            self._device_id,
            self._zone_number,
        )
        return {
            "root_depth_inches": values["root_depth_inches"],
            "soil_whc_in_per_in": values["soil_whc_in_per_in"],
            "mad": values["mad"],
            "kc": values["kc"],
            "trigger_buffer_inches": values["trigger_buffer_inches"],
            "formula": "capacity_in = root_depth_in * soil_whc_in_per_in * mad",
        }


class BhyveZoneSettingsSensor(BhyveZoneCoordinatorEntity, SensorEntity):
    """Sensor exposing raw zone settings needed for irrigation calculations."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:sprout"

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the zone settings sensor."""

        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"{device_id}_{zone_number}_settings"

    @property
    def native_value(self) -> str:
        """Return a compact summary of the zone profile."""

        zone = self.zone
        if zone is None:
            return "unavailable"
        return "configured"

    @property
    def name(self) -> str | None:
        """Return the entity name."""

        zone = self.zone
        if zone is None:
            return f"Zone {self._zone_number} settings"
        return f"{zone.name} settings"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose zone settings for later scheduling and water-balance logic."""

        zone = self.zone
        if zone is None:
            return {}
        attributes = _zone_settings_attributes(zone)
        runtime_key = f"{self._device_id}:{self._zone_number}"
        watering_profile = normalize_zone_watering_profile(
            self._entry.runtime_data.zone_watering_profiles.get(
                runtime_key,
                ZONE_WATERING_PROFILE_DEFAULT,
            )
        )
        sprinkler_wind_profile = self._entry.runtime_data.zone_sprinkler_wind_profiles.get(
            runtime_key,
            "Standard spray",
        )
        application_rate = self._entry.runtime_data.zone_application_rates.get(
            runtime_key,
            0.0,
        )
        agronomy = _resolved_zone_agronomy_values(
            self._entry,
            self._device_id,
            self._zone_number,
        )
        attributes["watering_profile"] = watering_profile
        attributes["sprinkler_head_type"] = sprinkler_wind_profile
        attributes["application_rate_inches_per_hour"] = round(float(application_rate), 2)
        attributes["application_rate_configured"] = float(application_rate) > 0
        attributes["root_depth_inches"] = agronomy["root_depth_inches"]
        attributes["soil_whc_in_per_in"] = agronomy["soil_whc_in_per_in"]
        attributes["mad"] = agronomy["mad"]
        attributes["kc"] = agronomy["kc"]
        attributes["trigger_buffer_inches"] = agronomy["trigger_buffer_inches"]
        attributes["capacity_inches"] = agronomy["capacity_inches"]
        attributes["lawn_mode"] = watering_profile == ZONE_WATERING_PROFILE_DEFAULT
        attributes["trees_shrubs_mode"] = watering_profile == ZONE_WATERING_PROFILE_TREES_SHRUBS
        attributes["water_efficient_planting"] = watering_profile == ZONE_WATERING_PROFILE_DROUGHT_TOLERANT
        attributes["vegetable_garden_mode"] = watering_profile == ZONE_WATERING_PROFILE_VEGETABLE_GARDEN
        return attributes


class BhyveSprinklerControllerStatusSensor(
    CoordinatorEntity[BhyveIrrigationSnapshot],
    SensorEntity,
):
    """Diagnostic sensor attached to a discovered sprinkler controller."""

    _attr_has_entity_name = True
    _attr_name = "API status"
    _attr_icon = "mdi:sprinkler-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        controller: BhyveSprinklerControllerSnapshot,
    ) -> None:
        """Initialize the controller status sensor."""

        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_{controller.device_id}_api_status"

    @property
    def available(self) -> bool:
        """Return whether the controller still exists in coordinator data."""

        return super().available and self._current_controller is not None

    @property
    def native_value(self) -> str:
        """Return the current API status."""

        controller = self._current_controller or self._controller
        if controller.last_error:
            if any(
                term in controller.last_error.lower()
                for term in ("signature", "signing secret")
            ):
                return "signature_invalid"
            return "error"
        if controller.available is True:
            return "online"
        if controller.available is False:
            return "offline"
        return "connected"

    @property
    def extra_state_attributes(self) -> dict[str, str | int]:
        """Return controller metadata that is safe to expose."""

        controller = self._current_controller or self._controller
        attributes = {
            "device_id": controller.device_id,
            "discovery": "direct_bhyve_api",
            "zone_count": len(controller.zones),
        }
        if controller.product_model:
            attributes["product_model"] = controller.product_model
        if controller.product_type:
            attributes["product_type"] = controller.product_type
        if controller.device_type:
            attributes["device_type"] = controller.device_type
        if controller.active_run is not None:
            attributes["active_zone_number"] = str(controller.active_run.zone_number)
        if controller.last_error:
            attributes["last_error"] = controller.last_error
        return attributes

    @property
    def device_info(self) -> DeviceInfo:
        """Create the sprinkler controller device entry."""

        controller = self._current_controller or self._controller
        return DeviceInfo(
            identifiers={(DOMAIN, controller.device_id)},
            manufacturer="Orbit B-hyve",
            model=controller.product_model or "Sprinkler Controller",
            name=controller.nickname,
            via_device=(DOMAIN, self._entry.entry_id),
        )

    @property
    def _current_controller(self) -> BhyveSprinklerControllerSnapshot | None:
        """Return the freshest controller snapshot from the coordinator."""

        for controller in self.coordinator.data.controllers:
            if controller.device_id == self._controller.device_id:
                return controller
        return None


class BhyveFallbackSprinklerStatusSensor(
    CoordinatorEntity[BhyveIrrigationSnapshot],
    SensorEntity,
):
    """Fallback sensor used when the account is authenticated but device typing is unclear."""

    _attr_has_entity_name = True
    _attr_name = "API status"
    _attr_icon = "mdi:sprinkler-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: BhyveSprinklersConfigEntry) -> None:
        """Initialize the fallback sprinkler sensor."""

        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_sprinkler_api_status"

    @property
    def native_value(self) -> str:
        """Return the current API status."""

        return "connected"

    @property
    def extra_state_attributes(self) -> dict[str, str | int]:
        """Return safe metadata explaining the fallback device registration."""

        return {
            "sprinkler_controller_count": self.coordinator.data.device_count,
            "discovery": "direct_bhyve_api_fallback",
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Create a fallback sprinkler controller device entry."""

        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}_sprinkler_controller")},
            manufacturer="Orbit B-hyve",
            model="Sprinkler Controller",
            name="B-hyve Sprinkler Controller",
            via_device=(DOMAIN, self._entry.entry_id),
        )


def _zone_settings_attributes(zone: BhyveSprinklerZone) -> dict[str, object]:
    """Return the full zone settings payload that drives irrigation logic."""

    attributes: dict[str, object] = {
        "device_id": zone.device_id,
        "zone_id": zone.zone_id,
        "zone_number": zone.zone_number,
        "zone_name": zone.name,
        "enabled": zone.enabled,
        "area": zone.area,
        "crop_type": zone.crop_type,
        "crop_coefficient": zone.crop_coefficient,
        "manual_crop_coefficient": zone.manual_crop_coefficient,
        "root_depth": zone.root_depth,
        "manual_root_depth": zone.manual_root_depth,
        "available_water_capacity": zone.available_water_capacity,
        "manage_allow_depletion": zone.manage_allow_depletion,
        "exposure_type": zone.exposure_type,
        "soil_type": zone.soil_type,
        "slope_type": zone.slope_type,
        "nozzle_type": zone.nozzle_type,
        "flow_rate": zone.flow_rate,
        "efficiency": zone.efficiency,
        "number_of_sprinkler_heads": zone.number_of_sprinkler_heads,
        "reported_soil_moisture_pct": zone.soil_moisture_level_at_end_of_day_pct,
        "garden_subtypes": [
            {"subtype": item.subtype, "plant_date": item.plant_date}
            for item in zone.garden_subtypes
        ],
        "tree_subtypes": [
            {"subtype": item.subtype, "plant_date": item.plant_date}
            for item in zone.tree_subtypes
        ],
    }
    if zone.wired is not None:
        attributes["wired"] = zone.wired
    if zone.smart_duration is not None:
        attributes["smart_duration"] = zone.smart_duration
    if zone.quickrun_duration is not None:
        attributes["quickrun_duration"] = zone.quickrun_duration
    if zone.smart_schedule_id:
        attributes["smart_schedule_id"] = zone.smart_schedule_id
    if zone.zone_disable_reason:
        attributes["zone_disable_reason"] = zone.zone_disable_reason
    if zone.latest_event is not None:
        attributes["latest_event"] = {
            "duration": zone.latest_event.duration,
            "end_local": zone.latest_event.end_local,
            "end_ts": zone.latest_event.end_ts,
            "schedule_name": zone.latest_event.schedule_name,
            "schedule_type": zone.latest_event.schedule_type,
        }
    if zone.recent_events:
        attributes["recent_events"] = [
            {
                "duration": item.duration,
                "end_local": item.end_local,
                "end_ts": item.end_ts,
                "schedule_name": item.schedule_name,
                "schedule_type": item.schedule_type,
            }
            for item in zone.recent_events
        ]
    if zone.schedules:
        attributes["schedules"] = [
            {
                "schedule_type": item.schedule_type,
                "schedule_id": item.schedule_id,
                "schedule_name": item.schedule_name,
            }
            for item in zone.schedules
        ]

    return attributes


def _resolved_zone_agronomy_values(
    entry: BhyveSprinklersConfigEntry,
    device_id: str,
    zone_number: int,
) -> dict[str, float]:
    """Return the explicit agronomy values currently active for a zone."""

    runtime_key = f"{device_id}:{zone_number}"
    profile = normalize_zone_watering_profile(
        entry.runtime_data.zone_watering_profiles.get(
            runtime_key,
            ZONE_WATERING_PROFILE_DEFAULT,
        )
    )
    defaults = ZONE_AGRONOMY_DEFAULTS.get(
        profile,
        ZONE_AGRONOMY_DEFAULTS[ZONE_WATERING_PROFILE_DEFAULT],
    )
    root_depth_inches = float(
        entry.runtime_data.zone_root_depths.get(
            runtime_key,
            defaults["root_depth_in"],
        )
    )
    soil_whc_in_per_in = float(
        entry.runtime_data.zone_soil_whc.get(
            runtime_key,
            defaults["soil_whc_in_per_in"],
        )
    )
    mad = float(
        entry.runtime_data.zone_mad_values.get(
            runtime_key,
            defaults["mad"],
        )
    )
    kc = float(
        entry.runtime_data.zone_kc_values.get(
            runtime_key,
            defaults["kc"],
        )
    )
    trigger_buffer_inches = float(
        entry.runtime_data.zone_trigger_buffers.get(
            runtime_key,
            DEFAULT_ZONE_TRIGGER_BUFFER_INCHES,
        )
    )
    capacity_inches = round(root_depth_inches * soil_whc_in_per_in * mad, 3)
    return {
        "root_depth_inches": round(root_depth_inches, 2),
        "soil_whc_in_per_in": round(soil_whc_in_per_in, 3),
        "mad": round(mad, 3),
        "kc": round(kc, 3),
        "trigger_buffer_inches": round(trigger_buffer_inches, 3),
        "capacity_inches": capacity_inches,
    }


def _controller_recent_events(
    controller: BhyveSprinklerControllerSnapshot,
    *,
    max_days: int = 21,
    max_events: int = 40,
) -> list[dict[str, object]]:
    """Return recent controller watering events merged across zones."""

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_days)
    events: list[dict[str, object]] = []
    seen: set[tuple[int, int | None, int | None, str | None, str | None]] = set()

    for zone in controller.zones:
        for item in merged_zone_recent_events(zone):
            event_key = (
                zone.zone_number,
                item.end_ts,
                item.duration,
                item.schedule_name,
                item.schedule_type,
            )
            if event_key in seen:
                continue
            seen.add(event_key)

            end_local = item.end_local
            end_sort = item.end_ts or 0
            if item.end_ts is not None:
                end_dt = datetime.fromtimestamp(item.end_ts, tz=timezone.utc)
                if end_dt < cutoff:
                    continue
                end_local = dt_util.as_local(end_dt).strftime("%Y-%m-%d %H:%M")
                end_sort = item.end_ts

            events.append(
                {
                    "zone_name": zone.name,
                    "zone_number": zone.zone_number,
                    "duration_minutes": round((item.duration or 0) / 60),
                    "end_local": end_local,
                    "schedule_name": item.schedule_name,
                    "schedule_type": item.schedule_type,
                    "end_ts": end_sort,
                }
            )

    events.sort(key=lambda item: int(item.get("end_ts", 0)), reverse=True)
    for event in events:
        event.pop("end_ts", None)
    return events[:max_events]


def _parse_time_string(value: str | None) -> dt_time | None:
    """Parse an HH:MM time string."""

    if not value or value == "unavailable":
        return None
    try:
        return dt_time.fromisoformat(value)
    except ValueError:
        return None


def _compact_zone_name(name: str) -> str:
    """Return a compact zone label for dashboard summary rows."""

    compact = name.replace("Front yard ", "Front ")
    compact = compact.replace("Backyard ", "Back ")
    compact = compact.replace("(", "").replace(")", "")
    compact = compact.replace("Middle-strip", "Middle")
    compact = compact.replace("driveway", "Drive")
    return compact


def _overview_profile_label(profile: str) -> str:
    """Return a short profile label for overview entity rows."""

    profile = normalize_zone_watering_profile(profile)
    if profile == ZONE_WATERING_PROFILE_DISABLED:
        return "Disabled"
    if profile == ZONE_WATERING_PROFILE_DROUGHT_TOLERANT:
        return "Drought"
    if profile == ZONE_WATERING_PROFILE_VEGETABLE_GARDEN:
        return "Veg"
    if profile == ZONE_WATERING_PROFILE_TREES_SHRUBS:
        return "Trees"
    if profile == ZONE_WATERING_PROFILE_ANNUAL_FLOWERS:
        return "Flowers"
    if profile == ZONE_WATERING_PROFILE_NATIVE_XERISCAPE:
        return "Xeric"
    return "Lawn"


def _overview_profile_icon(profile: str) -> str:
    """Return the profile icon used by overview entity rows."""

    profile = normalize_zone_watering_profile(profile)
    if profile == ZONE_WATERING_PROFILE_DISABLED:
        return "mdi:cancel"
    if profile == ZONE_WATERING_PROFILE_DROUGHT_TOLERANT:
        return "mdi:leaf"
    if profile == ZONE_WATERING_PROFILE_VEGETABLE_GARDEN:
        return "mdi:carrot"
    if profile == ZONE_WATERING_PROFILE_TREES_SHRUBS:
        return "mdi:tree-outline"
    if profile == ZONE_WATERING_PROFILE_ANNUAL_FLOWERS:
        return "mdi:flower"
    if profile == ZONE_WATERING_PROFILE_NATIVE_XERISCAPE:
        return "mdi:cactus"
    return "mdi:sprinkler-variant"


def _overview_application_rate_label(application_rate: float | None) -> str:
    """Return the live overview label for a measured application rate."""

    rate = float(application_rate or 0.0)
    if rate <= 0:
        return "needs calibration"
    return f"{rate:.2f} in/hr"


def _projected_cycle(controller_plan) -> dict[str, object]:
    """Return the projected next watering cycle from the active planner state."""

    if controller_plan.next_cycle_start is not None and controller_plan.next_cycle_end is not None:
        start_dt = dt_util.parse_datetime(controller_plan.next_cycle_start)
        end_dt = dt_util.parse_datetime(controller_plan.next_cycle_end)
        if start_dt is None:
            start_dt = datetime.fromisoformat(controller_plan.next_cycle_start)
        if end_dt is None:
            end_dt = datetime.fromisoformat(controller_plan.next_cycle_end)
        start_local = dt_util.as_local(start_dt)
        end_local = dt_util.as_local(end_dt)
        return {
            "status": controller_plan.next_cycle_status,
            "start_local": start_local.strftime("%Y-%m-%d %H:%M"),
            "end_local": end_local.strftime("%Y-%m-%d %H:%M"),
            "day_offset": max(0, (start_local.date() - dt_util.now().date()).days),
        }

    start_time = _parse_time_string(controller_plan.effective_start_time)
    end_time = _parse_time_string(controller_plan.effective_end_time)
    if start_time is None or end_time is None:
        return {
            "status": "unavailable",
            "start_local": None,
            "end_local": None,
            "day_offset": None,
        }

    now_local = dt_util.now()
    day_offset = 0
    status = controller_plan.decision
    if controller_plan.decision == "skip":
        return {
            "status": "not_scheduled",
            "start_local": None,
            "end_local": None,
            "day_offset": None,
        }
    if controller_plan.decision == "not_configured":
        return {
            "status": "not_configured",
            "start_local": None,
            "end_local": None,
            "day_offset": None,
        }
    if controller_plan.decision == "defer":
        day_offset = 1
        status = "forecast_hold"
    elif controller_plan.decision == "rain_delay":
        day_offset = max(1, int(controller_plan.rain_delay_days))
        status = "rain_delay"
    elif now_local.time() > end_time:
        day_offset = 1

    target_date = now_local.date() + timedelta(days=day_offset)
    start_local = f"{target_date.isoformat()} {start_time.strftime('%H:%M')}"
    end_local = f"{target_date.isoformat()} {end_time.strftime('%H:%M')}"
    return {
        "status": status,
        "start_local": start_local,
        "end_local": end_local,
        "day_offset": day_offset,
    }


def _projected_cycle_status_label(
    status: str,
    *,
    automatic_watering_enabled: bool = True,
) -> str:
    """Return a human-friendly label for the projected cycle status."""

    mapping = {
        "scheduled_today": "Watering scheduled today",
        "in_current_window": "Watering window open",
        "next_daily_window": "Next daily window",
        "rain_delay": "Skipping: Rain delay active",
        "forecast_hold": "Skipping: Forecasted precipitation",
        "restricted_day": "Skipping: Restricted watering day",
        "weather_hold": "Skipping: Weather hold",
        "not_scheduled": "Skipping: No watering needed",
        "not_configured": "Planner not configured",
        "unavailable": "Unavailable",
    }
    if status == "monitor_only":
        if automatic_watering_enabled:
            return "No watering today, monitoring for changes"
        return "Auto Watering Disabled"
    return mapping.get(status, status.replace("_", " ").strip().title())


def _projected_cycle_reason(
    status: str,
    default_reason: str | None,
    *,
    automatic_watering_enabled: bool,
) -> str | None:
    """Return a dashboard-facing explanation for the projected cycle state."""

    if status == "monitor_only" and not automatic_watering_enabled:
        return (
            "Automatic watering is turned off, so the planner is monitoring "
            "conditions without scheduling runs."
        )
    return default_reason


def _active_zone_plans_for_summary(controller_plan) -> list:
    """Return enabled zone plans that should count toward controller summaries."""

    return [
        zone_plan
        for zone_plan in controller_plan.zone_plans
        if zone_plan.enabled
        and normalize_zone_watering_profile(zone_plan.watering_profile)
        != ZONE_WATERING_PROFILE_DISABLED
    ]


def _average_zone_deficit(controller_plan) -> float | None:
    """Return the average deficit across active zones."""

    zone_plans = _active_zone_plans_for_summary(controller_plan)
    if not zone_plans:
        return None
    return round(
        sum(float(zone_plan.deficit_inches) for zone_plan in zone_plans) / len(zone_plans),
        3,
    )


def _average_zone_raw_deficit(controller_plan) -> float | None:
    """Return the average raw deficit across active zones."""

    zone_plans = _active_zone_plans_for_summary(controller_plan)
    if not zone_plans:
        return None
    return round(
        sum(float(zone_plan.raw_deficit_inches) for zone_plan in zone_plans) / len(zone_plans),
        3,
    )


def _highest_zone_plan_for_summary(controller_plan):
    """Return the most positive active-zone deficit plan for controller summaries."""

    zone_plans = _active_zone_plans_for_summary(controller_plan)
    if not zone_plans:
        return None
    return max(zone_plans, key=lambda zone_plan: float(zone_plan.deficit_inches))


def _estimated_next_need(
    entry: BhyveSprinklersConfigEntry,
    controller_plan,
    projected: dict[str, object],
) -> dict[str, object]:
    """Estimate when watering will likely be needed again from the current bucket state."""

    start_time = _parse_time_string(controller_plan.effective_start_time)
    end_time = _parse_time_string(controller_plan.effective_end_time)
    if start_time is None or end_time is None:
        return {
            "start_local": None,
            "end_local": None,
            "day_offset": None,
            "daily_recovery_inches": None,
        }

    if controller_plan.decision == "not_configured" or (
        controller_plan.decision == "skip"
        and controller_plan.peak_deficit_zone_name is None
    ):
        return {
            "start_local": None,
            "end_local": None,
            "day_offset": None,
            "daily_recovery_inches": None,
        }

    now_local = dt_util.now()
    active_zone_plans = _active_zone_plans_for_summary(controller_plan)
    if not active_zone_plans:
        return {
            "start_local": None,
            "end_local": None,
            "day_offset": None,
            "daily_recovery_inches": None,
        }

    if any(
        bool(zone_plan.trigger_active)
        for zone_plan in active_zone_plans
    ):
        return {
            "start_local": projected.get("start_local"),
            "end_local": projected.get("end_local"),
            "day_offset": projected.get("day_offset"),
            "daily_recovery_inches": None,
        }

    estimated_candidates = [
        _estimate_zone_next_need(entry, controller_plan, zone_plan)
        for zone_plan in active_zone_plans
    ]
    valid_candidates = [
        candidate
        for candidate in estimated_candidates
        if candidate["start_dt"] is not None
    ]
    if valid_candidates:
        best_candidate = min(
            valid_candidates,
            key=lambda candidate: candidate["start_dt"],
        )
        return {
            "start_local": best_candidate["start_local"],
            "end_local": best_candidate["end_local"],
            "day_offset": best_candidate["day_offset"],
            "daily_recovery_inches": best_candidate["daily_recovery_inches"],
        }

    highest_zone_plan = _highest_zone_plan_for_summary(controller_plan)
    if highest_zone_plan is None:
        return {
            "start_local": None,
            "end_local": None,
            "day_offset": None,
            "daily_recovery_inches": None,
        }

    daily_depletion_inches = max(
        float(highest_zone_plan.weekly_target_inches or 0.0) / 7.0,
        0.0,
    )
    current_water_inches = float(highest_zone_plan.current_water_inches or 0.0)
    trigger_buffer_inches = float(highest_zone_plan.trigger_buffer_inches or 0.0)
    days_until_due = highest_zone_plan.days_until_due
    earliest_offset = int(projected.get("day_offset") or 0)

    if controller_plan.decision == "skip":
        earliest_offset = max(1, earliest_offset)
    elif controller_plan.decision in {"rain_delay", "defer", "weather_hold", "restricted_day"}:
        earliest_offset = max(1, earliest_offset)

    depletion_offset = 0
    if days_until_due is None and daily_depletion_inches > 0 and current_water_inches > trigger_buffer_inches:
        days_until_due = max(
            0.0,
            (current_water_inches - trigger_buffer_inches) / daily_depletion_inches,
        )
    if days_until_due is not None:
        depletion_offset = max(0, math.ceil(float(days_until_due)))

    needed_offset = max(earliest_offset, depletion_offset)
    target_date = now_local.date() + timedelta(days=needed_offset)
    return {
        "start_local": f"{target_date.isoformat()} {start_time.strftime('%H:%M')}",
        "end_local": f"{target_date.isoformat()} {end_time.strftime('%H:%M')}",
        "day_offset": needed_offset,
        "daily_recovery_inches": round(daily_depletion_inches, 3) if daily_depletion_inches > 0 else None,
    }


def _estimate_zone_next_need(
    entry: BhyveSprinklersConfigEntry,
    controller_plan,
    zone_plan,
) -> dict[str, object]:
    """Estimate the next allowed window where a zone will hit its trigger buffer."""

    runtime_data = entry.runtime_data
    coordinator = runtime_data.coordinator
    controller = (
        coordinator.get_controller(controller_plan.device_id)
        if coordinator is not None
        else None
    )
    start_time = _parse_time_string(controller_plan.effective_start_time)
    end_time = _parse_time_string(controller_plan.effective_end_time)
    if controller is None or start_time is None or end_time is None:
        return {
            "start_dt": None,
            "start_local": None,
            "end_local": None,
            "day_offset": None,
            "daily_recovery_inches": None,
        }

    zone_hourly_et_inches = max(0.0, float(zone_plan.zone_hourly_et_inches or 0.0))
    current_water_inches = float(zone_plan.current_water_inches or 0.0)
    trigger_buffer_inches = float(zone_plan.trigger_buffer_inches or 0.0)
    deficit_inches = float(zone_plan.deficit_inches or 0.0)
    now_local = dt_util.now()
    search_now = now_local
    maximum_search_days = 45

    for _ in range(maximum_search_days + 2):
        next_window_start, _offset_days = compute_next_trigger_horizon(
            now_local=search_now,
            controller=controller,
            latitude=float(controller_plan.location_latitude),
            longitude=float(controller_plan.location_longitude),
            automatic_window_enabled=bool(controller_plan.automatic_window_enabled),
            automatic_window_preference=str(controller_plan.automatic_window_preference),
            effective_start_time=start_time,
            effective_end_time=end_time,
            zone_watering_profiles=runtime_data.zone_watering_profiles,
            controller_day_restrictions=runtime_data.controller_watering_day_restrictions,
            zone_day_restrictions=runtime_data.zone_watering_day_restrictions,
        )
        projected_draw_inches, projected_daylight_hours = project_et_draw(
            zone_hourly_et_inches,
            now_local,
            next_window_start,
        )
        projected_remaining_inches = current_water_inches - projected_draw_inches
        if projected_daylight_hours == 0:
            trigger_active = deficit_inches >= trigger_buffer_inches
        else:
            trigger_active = projected_remaining_inches <= trigger_buffer_inches

        if trigger_active:
            target_date = next_window_start.date()
            if bool(controller_plan.automatic_window_enabled):
                if str(controller_plan.automatic_window_preference) == AUTOMATIC_WINDOW_PREFERENCE_MORNING:
                    end_dt = next_window_start
                    start_dt = datetime.combine(
                        target_date,
                        start_time,
                        tzinfo=next_window_start.tzinfo,
                    )
                else:
                    start_dt = next_window_start
                    end_dt = datetime.combine(
                        target_date,
                        end_time,
                        tzinfo=next_window_start.tzinfo,
                    )
            else:
                start_dt = datetime.combine(
                    target_date,
                    start_time,
                    tzinfo=next_window_start.tzinfo,
                )
                end_dt = datetime.combine(
                    target_date,
                    end_time,
                    tzinfo=next_window_start.tzinfo,
                )
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)

            day_offset = max(0, (target_date - now_local.date()).days)
            return {
                "start_dt": start_dt,
                "start_local": f"{target_date.isoformat()} {start_dt.strftime('%H:%M')}",
                "end_local": f"{target_date.isoformat()} {end_dt.strftime('%H:%M')}",
                "day_offset": day_offset,
                "daily_recovery_inches": round(float(zone_plan.zone_daily_et_inches or 0.0), 3),
            }

        if (next_window_start.date() - now_local.date()).days >= maximum_search_days:
            break
        search_now = next_window_start + timedelta(minutes=1)

    return {
        "start_dt": None,
        "start_local": None,
        "end_local": None,
        "day_offset": None,
        "daily_recovery_inches": round(float(zone_plan.zone_daily_et_inches or 0.0), 3),
    }


def _account_device_info(entry: BhyveSprinklersConfigEntry) -> DeviceInfo:
    """Return shared account-level device info."""

    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Orbit B-hyve",
        model="Cloud Account",
        name="B-hyve Account",
    )
