"""Shared data models for the B-hyve Auto Sprinklers Controller integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dt_time
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

if TYPE_CHECKING:
    from .api import BhyveApiClient
    from .irrigation_api import BhyveIrrigationApi
    from .ledger import BhyveWaterBalanceStore
    from .plan_coordinator import BhyveIrrigationPlanCoordinator


@dataclass(slots=True)
class BhyveSprinklerController:
    """A discovered sprinkler controller available on the B-hyve account."""

    mac: str
    nickname: str
    product_model: str | None
    product_type: str | None
    device_type: str | None
    available: bool | None


@dataclass(slots=True)
class BhyveLatestEvent:
    """Summary of the latest watering event for a zone."""

    duration: int | None
    end_local: str | None
    end_ts: int | None
    schedule_name: str | None
    schedule_type: str | None


@dataclass(slots=True)
class BhyveScheduleSummary:
    """Summary of a B-hyve irrigation schedule attached to a zone."""

    schedule_type: str | None
    schedule_id: str | None
    schedule_name: str | None


@dataclass(slots=True)
class BhyvePlantSubtype:
    """Plant subtype metadata attached to a zone."""

    subtype: str | None
    plant_date: str | None


@dataclass(slots=True)
class BhyveSprinklerZone:
    """A B-hyve sprinkler zone."""

    device_id: str
    zone_id: str
    zone_number: int
    name: str
    enabled: bool
    area: float | None
    crop_type: str | None
    crop_coefficient: float | None
    manual_crop_coefficient: float | None
    root_depth: float | None
    manual_root_depth: float | None
    available_water_capacity: float | None
    manage_allow_depletion: float | None
    exposure_type: str | None
    soil_type: str | None
    slope_type: str | None
    nozzle_type: str | None
    flow_rate: float | None
    efficiency: float | None
    number_of_sprinkler_heads: int | None
    wired: bool | None
    smart_duration: int | None
    quickrun_duration: int | None
    smart_schedule_id: str | None
    soil_moisture_level_at_end_of_day_pct: float | None
    zone_disable_reason: str | None
    garden_subtypes: tuple[BhyvePlantSubtype, ...]
    tree_subtypes: tuple[BhyvePlantSubtype, ...]
    latest_event: BhyveLatestEvent | None
    recent_events: tuple[BhyveLatestEvent, ...]
    schedules: tuple[BhyveScheduleSummary, ...]


def merged_zone_recent_events(zone: BhyveSprinklerZone) -> tuple[BhyveLatestEvent, ...]:
    """Return B-hyve zone events merged across latest_event and recent_events."""

    merged: list[BhyveLatestEvent] = []
    seen: set[tuple[int | None, int | None, str | None, str | None]] = set()

    for event in (
        ((zone.latest_event,) if zone.latest_event is not None else ())
        + zone.recent_events
    ):
        event_key = (
            event.end_ts,
            event.duration,
            event.schedule_name,
            event.schedule_type,
        )
        if event_key in seen:
            continue
        seen.add(event_key)
        merged.append(event)

    merged.sort(key=lambda item: item.end_ts or 0, reverse=True)
    return tuple(merged)


@dataclass(slots=True)
class BhyveActiveRun:
    """Optimistic view of an in-progress quick run."""

    zone_number: int
    duration: int
    started_at: datetime
    expected_end: datetime
    source: str = "quick_run"


@dataclass(slots=True)
class BhyveSprinklerControllerSnapshot:
    """Current sprinkler controller state known to Home Assistant."""

    device_id: str
    nickname: str
    product_model: str | None
    product_type: str | None
    device_type: str | None
    available: bool | None
    zones: tuple[BhyveSprinklerZone, ...]
    active_run: BhyveActiveRun | None
    last_error: str | None = None


@dataclass(slots=True)
class BhyveIrrigationSnapshot:
    """Snapshot published by the irrigation coordinator."""

    device_count: int
    controllers: tuple[BhyveSprinklerControllerSnapshot, ...]


@dataclass(slots=True)
class BhyveDailyWaterBalance:
    """Persisted daily weather contribution for rolling water-balance math."""

    date: str
    raw_rain_inches: float
    effective_rain_inches: float
    et_inches: float


@dataclass(slots=True)
class BhyveControllerRecentRun:
    """Aggregated recent watering event attached to a controller."""

    zone_number: int
    zone_name: str
    duration_minutes: int
    end_local: str | None
    end_ts: int | None
    schedule_name: str | None
    schedule_type: str | None


@dataclass(slots=True)
class BhyveZoneBucketState:
    """Persisted allowable-depletion bucket state for a zone."""

    capacity_inches: float
    current_water_inches: float
    last_bucket_update: str | None
    last_et_hour_key: str | None
    last_authoritative_et_date: str | None
    last_effective_rain_date: str | None
    last_effective_rain_total_inches: float
    last_irrigation_event_key: str | None


@dataclass(slots=True)
class BhyveZonePlan:
    """Recommended irrigation output for a single zone."""

    device_id: str
    zone_number: int
    zone_name: str
    enabled: bool
    application_rate_configured: bool
    application_rate_inches_per_hour: float
    root_depth_inches: float | None
    soil_whc_in_per_in: float | None
    mad: float | None
    kc: float | None
    capacity_inches: float
    current_water_inches: float
    deficit_inches: float
    raw_deficit_inches: float
    trigger_buffer_inches: float
    projected_et_draw_inches: float
    projected_daylight_hours: float
    projected_remaining_inches: float
    zone_hourly_et_inches: float
    zone_daily_et_inches: float
    trigger_active: bool
    requested_runtime_minutes: int
    recommended_runtime_minutes: int
    minimum_run_threshold_minutes: int
    effective_minimum_run_threshold_minutes: int
    runtime_bank_minutes: int
    runtime_bank_increment_minutes: int
    cycle_minutes: tuple[int, ...]
    scale_factor: float
    weekly_target_inches: float
    estimated_application_inches: float | None
    recent_runtime_minutes_7d: int
    recent_irrigation_inches_7d: float
    remaining_weekly_runtime_minutes: int | None
    capped_by_session_limit: bool
    capped_by_weekly_limit: bool
    allowable_depletion_inches: float | None
    crop_coefficient: float
    user_watering_coefficient: float
    zone_demand_multiplier: float
    weekday_name: str
    controller_day_restriction: str
    zone_day_restriction: str
    schedule_hold_active: bool
    allowed_days_per_week: int
    sprinkler_head_type: str
    effective_max_watering_wind_speed_mph: float | None
    max_watering_gust_speed_mph: float | None
    weather_hold_active: bool
    exposure_factor: float
    seasonal_factor: float
    soil_storage_factor: float
    storage_buffer_days: float | None
    session_limit_minutes: int
    watering_profile: str
    water_efficient_mode: bool
    trees_shrubs_mode: bool
    vegetable_garden_mode: bool
    banked_by_weather_hold: bool
    target_interval_days: int | None
    days_since_last_watering: float | None
    days_until_due: float | None
    forced_by_skip_limit: bool
    deferred_by_window_limit: bool
    reason: str


@dataclass(slots=True)
class BhyveControllerPlan:
    """Derived irrigation-planning state for a controller."""

    device_id: str
    nickname: str
    product_model: str | None
    decision: str
    reason: str
    deficit_inches: float
    raw_deficit_inches: float
    deficit_basis: str
    peak_deficit_zone_name: str | None
    effective_rain_24h_inches: float
    rain_active_hours_24h: float | None
    average_rain_rate_inches_per_hour: float | None
    hourly_et_inches: float
    et_source: str
    effective_rain_7d_inches: float
    raw_rain_7d_inches: float
    et_today_inches: float
    et_7d_inches: float
    irrigation_7d_inches: float
    irrigation_7d_minutes: int
    weekly_target_inches: float
    et_multiplier: float
    location_latitude: float
    location_longitude: float
    location_source: str
    temperature_f: float | None
    humidity_percent: float | None
    wind_speed_mph: float | None
    wind_gust_mph: float | None
    min_watering_temperature_f: float
    max_watering_wind_speed_mph: float
    effective_max_watering_wind_speed_mph: float
    max_watering_gust_speed_mph: float | None
    sprinkler_wind_profile: str
    effective_wind_profile: str
    weather_hold_active: bool
    weather_stop_held_today: bool
    rain_delay_days: int
    dry_days_streak: int
    next_cycle_start: str | None
    next_cycle_end: str | None
    next_cycle_status: str
    next_cycle_reason: str
    last_watering_end: str | None
    last_watering_zone_name: str | None
    last_watering_duration_minutes: int | None
    recent_runtime_minutes_14d: int
    recent_runtime_minutes_21d: int
    recent_run_count_14d: int
    recent_run_count_21d: int
    recent_runs: tuple[BhyveControllerRecentRun, ...]
    automatic_window_enabled: bool
    automatic_window_preference: str
    automatic_window_max_minutes: int
    suggested_start_time: str
    suggested_end_time: str
    effective_start_time: str
    effective_end_time: str
    available_window_minutes: int
    automatic_window_reason: str
    current_weekday_name: str
    controller_day_restriction: str
    allowed_days_per_week: int
    total_requested_runtime_minutes: int
    total_recommended_runtime_minutes: int
    window_rotation_applied: bool
    allowed_now: bool
    weather_source_status: str
    forecast_rain_amount_inches: float | None
    forecast_rain_probability: float | None
    last_evaluated: str
    zone_plans: tuple[BhyveZonePlan, ...]


@dataclass(slots=True)
class BhyveIrrigationPlanSnapshot:
    """Snapshot of derived irrigation-planning data."""

    controllers: tuple[BhyveControllerPlan, ...]
    forecast_source: str | None = None
    forecast_rain_amount_inches: float | None = None
    forecast_rain_probability: float | None = None
    weather_source_status: str = "not_configured"


@dataclass(slots=True)
class BhyveRuntimeData:
    """Runtime objects attached to a config entry."""

    client: "BhyveApiClient"
    irrigation_api: "BhyveIrrigationApi"
    coordinator: DataUpdateCoordinator[BhyveIrrigationSnapshot]
    water_balance_store: "BhyveWaterBalanceStore"
    quick_run_durations: dict[str, int]
    zone_application_rates: dict[str, float]
    zone_root_depths: dict[str, float]
    zone_soil_whc: dict[str, float]
    zone_mad_values: dict[str, float]
    zone_kc_values: dict[str, float]
    zone_trigger_buffers: dict[str, float]
    max_weekly_run_times: dict[str, int]
    zone_watering_coefficients: dict[str, float]
    zone_watering_profiles: dict[str, str]
    zone_sprinkler_wind_profiles: dict[str, str]
    controller_watering_day_restrictions: dict[str, str]
    zone_watering_day_restrictions: dict[str, str]
    watering_window_times: dict[str, dt_time]
    automatic_window_preferences: dict[str, str]
    automatic_window_max_minutes: dict[str, int]
    overall_watering_coefficient: float
    minimum_run_threshold_minutes: int
    max_watering_wind_speed_mph: float
    min_watering_temperature_f: float
    automatic_watering_enabled: bool
    notifications_enabled: bool
    automatic_window_enabled: dict[str, bool]
    notification_service: str | None
    plan_coordinator: "BhyveIrrigationPlanCoordinator | None"
    automatic_run_tokens: dict[str, str]
    sunset_calc_failed_date: str | None
    last_sunset_notification_dates: dict[str, str]


BhyveSprinklersConfigEntry = ConfigEntry[BhyveRuntimeData]
