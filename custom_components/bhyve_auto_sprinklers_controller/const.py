"""Constants for the B-hyve Auto Sprinklers Controller integration."""

from __future__ import annotations

from datetime import time as dt_time, timedelta

from homeassistant.const import Platform

DOMAIN = "bhyve_auto_sprinklers_controller"

CONF_CONTROLLER_DEVICE_ID = "controller_device_id"
CONF_DAILY_RAIN_ENTITY_ID = "daily_rain_entity_id"
CONF_ET_ENTITY_ID = "et_entity_id"
CONF_FORECAST_RAIN_AMOUNT_ENTITY_ID = "forecast_rain_amount_entity_id"
CONF_FORECAST_RAIN_PROBABILITY_ENTITY_ID = "forecast_rain_probability_entity_id"
CONF_FORECAST_WEATHER_ENTITY_ID = "forecast_weather_entity_id"
CONF_HUMIDITY_ENTITY_ID = "humidity_entity_id"
CONF_IRRADIANCE_ENTITY_ID = "irradiance_entity_id"
CONF_NOTIFICATION_SERVICE = "notification_service"
CONF_PLANNER_LATITUDE = "planner_latitude"
CONF_PLANNER_LONGITUDE = "planner_longitude"
CONF_TEMPERATURE_ENTITY_ID = "temperature_entity_id"
CONF_UV_INDEX_ENTITY_ID = "uv_index_entity_id"
CONF_WIND_GUST_ENTITY_ID = "wind_gust_entity_id"
CONF_WIND_SPEED_ENTITY_ID = "wind_speed_entity_id"

DEVICE_TYPE_SPRINKLER = "sprinkler_timer"

ATTR_DEVICE_ID = "device_id"
ATTR_DURATION = "duration"
ATTR_ZONE_NUMBER = "zone_number"

SERVICE_QUICK_RUN_ZONE = "quick_run_zone"
SERVICE_RECALCULATE_PLAN = "recalculate_plan"
SERVICE_REFRESH_ZONES = "refresh_zones"
SERVICE_STOP_WATERING = "stop_watering"

DEFAULT_SCAN_INTERVAL = timedelta(minutes=5)
DEFAULT_PLAN_SCAN_INTERVAL = timedelta(minutes=15)
DEFAULT_STARTUP_REFRESH_DELAY = timedelta(seconds=20)
DEFAULT_AUTOMATIC_WATERING_ENABLED = False
DEFAULT_NOTIFICATIONS_ENABLED = False
DEFAULT_AUTOMATIC_WINDOW_ENABLED = True
DEFAULT_AUTOMATIC_WINDOW_PREFERENCE = "Morning (dawn)"
DEFAULT_QUICK_RUN_DURATION = 15 * 60
DEFAULT_MAX_WEEKLY_RUN_TIME = 0
DEFAULT_ZONE_APPLICATION_RATE_IN_PER_HOUR = 0.0
DEFAULT_ZONE_TRIGGER_BUFFER_INCHES = 0.05
DEFAULT_OVERALL_WATERING_COEFFICIENT = 1.0
DEFAULT_ZONE_WATERING_COEFFICIENT = 1.0
DEFAULT_MINIMUM_RUN_THRESHOLD_MINUTES = 10
DEFAULT_MAX_WATERING_WIND_SPEED_MPH = 12.0
DEFAULT_MIN_WATERING_TEMPERATURE_F = 40.0
DEFAULT_WATERING_END_TIME = dt_time(hour=23, minute=59)
DEFAULT_WATERING_START_TIME = dt_time(hour=0, minute=0)
DEFAULT_MAX_AUTOMATIC_WATERING_WINDOW_MINUTES = 480
DEFAULT_ZONE_MAX_SESSION_MINUTES = 45
DEFAULT_CYCLE_AND_SOAK_THRESHOLD_MINUTES = 30
CALIBRATION_RUN_DURATION_SECONDS = 900
DAY_RESTRICTION_AUTO = "Auto"
DAY_RESTRICTION_DISABLED = "Disabled"
WEEKDAY_KEYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
WEEKDAY_LABELS = {
    "monday": "Monday",
    "tuesday": "Tuesday",
    "wednesday": "Wednesday",
    "thursday": "Thursday",
    "friday": "Friday",
    "saturday": "Saturday",
    "sunday": "Sunday",
}
ZONE_WATERING_PROFILE_DEFAULT = "Default (lawn)"
ZONE_WATERING_PROFILE_DEFAULT_LEGACY = "Default"
ZONE_WATERING_PROFILE_DISABLED = "Disabled"
ZONE_WATERING_PROFILE_TREES_SHRUBS = "Trees / shrubs"
ZONE_WATERING_PROFILE_DROUGHT_TOLERANT = "Drought tolerant"
ZONE_WATERING_PROFILE_VEGETABLE_GARDEN = "Vegetable garden"
ZONE_WATERING_PROFILE_ANNUAL_FLOWERS = "Annual flowers / containers"
ZONE_WATERING_PROFILE_NATIVE_XERISCAPE = "Native / xeriscape"
SPRINKLER_WIND_PROFILE_STANDARD_SPRAY = "Standard spray"
SPRINKLER_WIND_PROFILE_ROTARY_STREAM = "Rotary / stream"
SPRINKLER_WIND_PROFILE_DRIP_BUBBLER = "Drip / bubbler"
DEFAULT_ZONE_SPRINKLER_WIND_PROFILE = SPRINKLER_WIND_PROFILE_STANDARD_SPRAY
AUTOMATIC_WINDOW_PREFERENCE_MORNING = "Morning (dawn)"
AUTOMATIC_WINDOW_PREFERENCE_EVENING = "Evening (sunset)"
MAX_QUICK_RUN_DURATION = 7200
MAX_WEEKLY_RUN_TIME = 10080
MAX_MINIMUM_RUN_THRESHOLD_MINUTES = 60
MAX_ZONE_WATERING_COEFFICIENT = 3.0
MIN_ZONE_WATERING_COEFFICIENT = 0.1
MAX_ZONE_APPLICATION_RATE_IN_PER_HOUR = 3.0
MIN_ZONE_ROOT_DEPTH_IN = 1.0
MAX_ZONE_ROOT_DEPTH_IN = 48.0
MIN_ZONE_SOIL_WHC_IN_PER_IN = 0.05
MAX_ZONE_SOIL_WHC_IN_PER_IN = 0.30
MIN_ZONE_MAD = 0.10
MAX_ZONE_MAD = 0.80
MIN_ZONE_KC = 0.10
MAX_ZONE_KC = 1.50
MIN_ZONE_TRIGGER_BUFFER_INCHES = 0.0
MAX_ZONE_TRIGGER_BUFFER_INCHES = 0.25
MAX_WATERING_WIND_SPEED_MPH = 35.0
MAX_WATERING_GUST_SPEED_MPH = 50.0
MIN_WATERING_TEMPERATURE_F = 20.0
MAX_WATERING_TEMPERATURE_F = 60.0
MIN_AUTOMATIC_WATERING_WINDOW_MINUTES = 60
MAX_AUTOMATIC_WATERING_WINDOW_MINUTES = 480
MIN_QUICK_RUN_DURATION = 1
ROTARY_WIND_TOLERANCE_BONUS_MPH = 3.0
SPRAY_GUST_THRESHOLD_OFFSET_MPH = 6.0
ROTARY_GUST_THRESHOLD_OFFSET_MPH = 7.0
SPRAY_GUST_THRESHOLD_FACTOR = 1.50
ROTARY_GUST_THRESHOLD_FACTOR = 1.45
DAILY_RAIN_ROLLOVER_GRACE_HOURS = 1
DAILY_RAIN_ROLLOVER_TOLERANCE_INCHES = 0.01
FORCE_MINIMUM_RUN_AFTER_DAYS = 7
WATER_BALANCE_WINDOW_DAYS = 7
MINIMUM_DEFICIT_TO_WATER_INCHES = 0.10
FORECAST_RAIN_DEFER_THRESHOLD_INCHES = 0.30
FORECAST_RAIN_DEFER_PROBABILITY = 70.0
FORECAST_RAIN_DEFER_DEFICIT_FACTOR = 0.90
DEFICIT_SMOOTHING_ALPHA = 0.45
DEFICIT_SMOOTHING_DEADBAND_INCHES = 0.02
DEFICIT_SMOOTHING_BYPASS_DELTA_INCHES = 0.20
DEFICIT_SMOOTHING_NEAR_ZERO_INCHES = 0.01
REFERENCE_LATITUDE = 40.5
ET_DAYLIGHT_START_HOUR = 5
ET_DAYLIGHT_END_HOUR = 22
ET_STALE_THRESHOLD = timedelta(minutes=90)
SOLAR_RADIATION_COVERAGE_THRESHOLD = 0.80
WATT_HOURS_M2_TO_MJ_M2 = 0.0036
WIND_SPEED_MPH_TO_MPS = 0.44704
MONTHLY_SOLAR_RADIATION_CLIMATOLOGY_MJ_M2: dict[int, float] = {
    1: 7.2,
    2: 10.1,
    3: 14.3,
    4: 18.3,
    5: 22.6,
    6: 25.8,
    7: 26.6,
    8: 23.1,
    9: 18.6,
    10: 13.0,
    11: 8.3,
    12: 6.5,
}

ZONE_AGRONOMY_DEFAULTS: dict[str, dict[str, float]] = {
    ZONE_WATERING_PROFILE_DEFAULT: {
        "root_depth_in": 14.0,
        "soil_whc_in_per_in": 0.15,
        "mad": 0.50,
        "kc": 1.0,
    },
    ZONE_WATERING_PROFILE_DROUGHT_TOLERANT: {
        "root_depth_in": 20.0,
        "soil_whc_in_per_in": 0.15,
        "mad": 0.40,
        "kc": 0.6,
    },
    ZONE_WATERING_PROFILE_VEGETABLE_GARDEN: {
        "root_depth_in": 8.0,
        "soil_whc_in_per_in": 0.20,
        "mad": 0.50,
        "kc": 1.0,
    },
    ZONE_WATERING_PROFILE_TREES_SHRUBS: {
        "root_depth_in": 30.0,
        "soil_whc_in_per_in": 0.15,
        "mad": 0.45,
        "kc": 0.7,
    },
    ZONE_WATERING_PROFILE_ANNUAL_FLOWERS: {
        "root_depth_in": 6.0,
        "soil_whc_in_per_in": 0.20,
        "mad": 0.55,
        "kc": 0.8,
    },
    ZONE_WATERING_PROFILE_NATIVE_XERISCAPE: {
        "root_depth_in": 24.0,
        "soil_whc_in_per_in": 0.12,
        "mad": 0.35,
        "kc": 0.4,
    },
}


def normalize_zone_watering_profile(profile: str | None) -> str:
    """Return a supported zone watering profile, preserving legacy defaults."""

    if not profile or profile == ZONE_WATERING_PROFILE_DEFAULT_LEGACY:
        return ZONE_WATERING_PROFILE_DEFAULT
    if profile in {
        ZONE_WATERING_PROFILE_DEFAULT,
        ZONE_WATERING_PROFILE_DISABLED,
        ZONE_WATERING_PROFILE_TREES_SHRUBS,
        ZONE_WATERING_PROFILE_DROUGHT_TOLERANT,
        ZONE_WATERING_PROFILE_VEGETABLE_GARDEN,
        ZONE_WATERING_PROFILE_ANNUAL_FLOWERS,
        ZONE_WATERING_PROFILE_NATIVE_XERISCAPE,
    }:
        return profile
    return ZONE_WATERING_PROFILE_DEFAULT


def normalize_day_restriction(mode: str | None) -> str:
    """Return a supported weekday restriction mode."""

    if mode == DAY_RESTRICTION_DISABLED:
        return DAY_RESTRICTION_DISABLED
    return DAY_RESTRICTION_AUTO


def normalize_automatic_window_preference(mode: str | None) -> str:
    """Return a supported automatic watering timing preference."""

    if mode == AUTOMATIC_WINDOW_PREFERENCE_EVENING:
        return AUTOMATIC_WINDOW_PREFERENCE_EVENING
    return AUTOMATIC_WINDOW_PREFERENCE_MORNING

MONTHLY_ET_REFERENCE: dict[int, dict[str, float]] = {
    1: {
        "daily_et_inches": 0.02,
        "weekly_target_inches": 0.10,
        "et_multiplier": 0.10,
        "reference_temp_f": 35.0,
    },
    2: {
        "daily_et_inches": 0.03,
        "weekly_target_inches": 0.15,
        "et_multiplier": 0.15,
        "reference_temp_f": 40.0,
    },
    3: {
        "daily_et_inches": 0.04,
        "weekly_target_inches": 0.25,
        "et_multiplier": 0.25,
        "reference_temp_f": 48.0,
    },
    4: {
        "daily_et_inches": 0.08,
        "weekly_target_inches": 0.55,
        "et_multiplier": 0.40,
        "reference_temp_f": 55.0,
    },
    5: {
        "daily_et_inches": 0.13,
        "weekly_target_inches": 0.90,
        "et_multiplier": 0.60,
        "reference_temp_f": 65.0,
    },
    6: {
        "daily_et_inches": 0.24,
        "weekly_target_inches": 1.75,
        "et_multiplier": 1.00,
        "reference_temp_f": 78.0,
    },
    7: {
        "daily_et_inches": 0.33,
        "weekly_target_inches": 2.35,
        "et_multiplier": 1.40,
        "reference_temp_f": 88.0,
    },
    8: {
        "daily_et_inches": 0.30,
        "weekly_target_inches": 2.15,
        "et_multiplier": 1.25,
        "reference_temp_f": 84.0,
    },
    9: {
        "daily_et_inches": 0.18,
        "weekly_target_inches": 1.20,
        "et_multiplier": 0.80,
        "reference_temp_f": 74.0,
    },
    10: {
        "daily_et_inches": 0.08,
        "weekly_target_inches": 0.50,
        "et_multiplier": 0.40,
        "reference_temp_f": 60.0,
    },
    11: {
        "daily_et_inches": 0.03,
        "weekly_target_inches": 0.15,
        "et_multiplier": 0.20,
        "reference_temp_f": 45.0,
    },
    12: {
        "daily_et_inches": 0.02,
        "weekly_target_inches": 0.10,
        "et_multiplier": 0.10,
        "reference_temp_f": 36.0,
    },
}

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.VALVE,
    Platform.NUMBER,
    Platform.BUTTON,
    Platform.TIME,
    Platform.SWITCH,
    Platform.SELECT,
]
