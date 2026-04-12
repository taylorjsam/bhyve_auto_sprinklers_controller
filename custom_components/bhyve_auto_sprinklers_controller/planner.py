"""Pure irrigation-planning calculations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time as dt_time, timedelta
import logging
from math import acos, ceil, cos, exp, pi, sin, sqrt, tan

from .const import (
    AUTOMATIC_WINDOW_PREFERENCE_EVENING,
    AUTOMATIC_WINDOW_PREFERENCE_MORNING,
    DAY_RESTRICTION_AUTO,
    DAY_RESTRICTION_DISABLED,
    DEFAULT_AUTOMATIC_WINDOW_PREFERENCE,
    DEFAULT_MAX_AUTOMATIC_WATERING_WINDOW_MINUTES,
    DEFAULT_CYCLE_AND_SOAK_THRESHOLD_MINUTES,
    DEFAULT_MINIMUM_RUN_THRESHOLD_MINUTES,
    DEFAULT_ZONE_TRIGGER_BUFFER_INCHES,
    DEFAULT_WATERING_END_TIME,
    DEFAULT_WATERING_START_TIME,
    DEFAULT_ZONE_MAX_SESSION_MINUTES,
    ET_DAYLIGHT_END_HOUR,
    ET_DAYLIGHT_START_HOUR,
    FORCE_MINIMUM_RUN_AFTER_DAYS,
    FORECAST_RAIN_DEFER_DEFICIT_FACTOR,
    FORECAST_RAIN_DEFER_PROBABILITY,
    FORECAST_RAIN_DEFER_THRESHOLD_INCHES,
    MAX_AUTOMATIC_WATERING_WINDOW_MINUTES,
    MAX_ZONE_TRIGGER_BUFFER_INCHES,
    MAX_MINIMUM_RUN_THRESHOLD_MINUTES,
    MAX_WATERING_GUST_SPEED_MPH,
    MAX_WATERING_TEMPERATURE_F,
    MAX_WATERING_WIND_SPEED_MPH,
    MAX_ZONE_WATERING_COEFFICIENT,
    MIN_AUTOMATIC_WATERING_WINDOW_MINUTES,
    MINIMUM_DEFICIT_TO_WATER_INCHES,
    MIN_ZONE_TRIGGER_BUFFER_INCHES,
    MIN_WATERING_TEMPERATURE_F,
    MIN_ZONE_WATERING_COEFFICIENT,
    MONTHLY_SOLAR_RADIATION_CLIMATOLOGY_MJ_M2,
    MONTHLY_ET_REFERENCE,
    REFERENCE_LATITUDE,
    ROTARY_GUST_THRESHOLD_FACTOR,
    ROTARY_GUST_THRESHOLD_OFFSET_MPH,
    ROTARY_WIND_TOLERANCE_BONUS_MPH,
    SOLAR_RADIATION_COVERAGE_THRESHOLD,
    SPRAY_GUST_THRESHOLD_FACTOR,
    SPRAY_GUST_THRESHOLD_OFFSET_MPH,
    SPRINKLER_WIND_PROFILE_DRIP_BUBBLER,
    SPRINKLER_WIND_PROFILE_ROTARY_STREAM,
    SPRINKLER_WIND_PROFILE_STANDARD_SPRAY,
    WATER_BALANCE_WINDOW_DAYS,
    WEEKDAY_KEYS,
    WEEKDAY_LABELS,
    WATT_HOURS_M2_TO_MJ_M2,
    WIND_SPEED_MPH_TO_MPS,
    normalize_automatic_window_preference,
    normalize_day_restriction,
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
from .models import (
    BhyveControllerPlan,
    BhyveControllerRecentRun,
    BhyveDailyWaterBalance,
    BhyveLatestEvent,
    BhyveSprinklerControllerSnapshot,
    BhyveSprinklerZone,
    BhyveZoneBucketState,
    BhyveZonePlan,
    merged_zone_recent_events,
)

_LOGGER = logging.getLogger(__name__)

_GALLON_TO_INCHES_PER_SQFT = 1.604
_SOLAR_CONSTANT_MJ_M2_MIN = 0.0820
_COOL_SEASON_GRASS_FACTORS = {
    1: 0.35,
    2: 0.40,
    3: 0.45,
    4: 0.45,
    5: 0.70,
    6: 0.95,
    7: 1.18,
    8: 1.12,
    9: 0.85,
    10: 0.60,
    11: 0.40,
    12: 0.35,
}
_WARM_SEASON_GRASS_FACTORS = {
    1: 0.10,
    2: 0.10,
    3: 0.18,
    4: 0.35,
    5: 0.65,
    6: 0.95,
    7: 1.12,
    8: 1.08,
    9: 0.82,
    10: 0.45,
    11: 0.18,
    12: 0.10,
}
_PERENNIAL_FACTORS = {
    1: 0.30,
    2: 0.35,
    3: 0.35,
    4: 0.35,
    5: 0.55,
    6: 0.80,
    7: 0.92,
    8: 0.90,
    9: 0.72,
    10: 0.50,
    11: 0.40,
    12: 0.30,
}
_GARDEN_FACTORS = {
    1: 0.10,
    2: 0.15,
    3: 0.20,
    4: 0.35,
    5: 0.65,
    6: 1.00,
    7: 1.22,
    8: 1.18,
    9: 0.90,
    10: 0.50,
    11: 0.25,
    12: 0.10,
}
_WATER_EFFICIENT_INTERVAL_DAYS = {
    1: 10,
    2: 10,
    3: 7,
    4: 7,
    5: 5,
    6: 4,
    7: 4,
    8: 4,
    9: 5,
    10: 7,
    11: 10,
    12: 10,
}
_TREES_SHRUBS_INTERVAL_DAYS = {
    1: 8,
    2: 8,
    3: 6,
    4: 5,
    5: 4,
    6: 3,
    7: 3,
    8: 3,
    9: 4,
    10: 5,
    11: 7,
    12: 8,
}
_VEGETABLE_GARDEN_INTERVAL_DAYS = {
    1: 5,
    2: 5,
    3: 4,
    4: 3,
    5: 2,
    6: 1,
    7: 1,
    8: 1,
    9: 2,
    10: 3,
    11: 4,
    12: 5,
}
_VEGETABLE_GARDEN_SESSION_CAP = {
    1: 10,
    2: 10,
    3: 10,
    4: 12,
    5: 15,
    6: 20,
    7: 20,
    8: 20,
    9: 15,
    10: 12,
    11: 10,
    12: 10,
}
_PROFILE_INTERVAL_MIN_EVENT_MINUTES = 5
_EFFECTIVE_RAIN_REFERENCE_RATE_IN_PER_HOUR = 0.12
_EFFECTIVE_RAIN_RATE_EXPONENT = 0.10
_EFFECTIVE_RAIN_RATE_MIN_FACTOR = 0.94
_EFFECTIVE_RAIN_RATE_MAX_FACTOR = 1.18
_EFFECTIVE_RAIN_MIN_ACTIVE_HOURS = 0.25


def clamp(value: float, minimum: float, maximum: float) -> float:
    """Clamp a value into a closed range."""

    return max(minimum, min(maximum, value))


def _fahrenheit_to_celsius(value_f: float) -> float:
    """Convert Fahrenheit to Celsius."""

    return (float(value_f) - 32.0) * (5.0 / 9.0)


def _saturation_vapor_pressure_kpa(temp_c: float) -> float:
    """Return saturation vapor pressure in kPa."""

    return 0.6108 * exp((17.27 * temp_c) / (temp_c + 237.3))


def _psychrometric_constant_kpa_per_c(elevation_m: float) -> float:
    """Return the psychrometric constant in kPa/C."""

    pressure_kpa = 101.3 * (((293.0 - (0.0065 * elevation_m)) / 293.0) ** 5.26)
    return 0.000665 * pressure_kpa


def _extraterrestrial_radiation_mj_m2_day(for_date: date, latitude: float) -> float:
    """Return daily extraterrestrial radiation for FAO-56."""

    day_of_year = for_date.timetuple().tm_yday
    latitude_radians = latitude * pi / 180.0
    inverse_relative_distance = 1.0 + (0.033 * cos((2.0 * pi / 365.0) * day_of_year))
    solar_declination = 0.409 * sin(((2.0 * pi / 365.0) * day_of_year) - 1.39)
    sunset_hour_angle = acos(
        clamp(-tan(latitude_radians) * tan(solar_declination), -1.0, 1.0)
    )
    return (
        (24.0 * 60.0 / pi)
        * _SOLAR_CONSTANT_MJ_M2_MIN
        * inverse_relative_distance
        * (
            (sunset_hour_angle * sin(latitude_radians) * sin(solar_declination))
            + (cos(latitude_radians) * cos(solar_declination) * sin(sunset_hour_angle))
        )
    )


def calc_fao56_daily_reference_et_inches(
    *,
    for_date: date,
    latitude: float,
    elevation_m: float,
    temperature_min_f: float,
    temperature_max_f: float,
    humidity_min_percent: float,
    humidity_max_percent: float,
    wind_speed_mph: float,
    solar_radiation_wh_m2: float,
) -> float:
    """Return FAO-56 daily reference ET in inches/day."""

    temp_min_c = _fahrenheit_to_celsius(temperature_min_f)
    temp_max_c = _fahrenheit_to_celsius(temperature_max_f)
    temp_mean_c = (temp_min_c + temp_max_c) / 2.0
    wind_speed_m_s = max(0.0, float(wind_speed_mph)) * WIND_SPEED_MPH_TO_MPS
    solar_radiation_mj_m2 = max(0.0, float(solar_radiation_wh_m2)) * WATT_HOURS_M2_TO_MJ_M2

    saturation_vapor_pressure_min = _saturation_vapor_pressure_kpa(temp_min_c)
    saturation_vapor_pressure_max = _saturation_vapor_pressure_kpa(temp_max_c)
    saturation_vapor_pressure = (
        saturation_vapor_pressure_min + saturation_vapor_pressure_max
    ) / 2.0
    actual_vapor_pressure = (
        (
            saturation_vapor_pressure_min
            * clamp(float(humidity_max_percent), 0.0, 100.0)
        )
        + (
            saturation_vapor_pressure_max
            * clamp(float(humidity_min_percent), 0.0, 100.0)
        )
    ) / 200.0
    slope_vapor_pressure_curve = (
        4098.0
        * _saturation_vapor_pressure_kpa(temp_mean_c)
        / ((temp_mean_c + 237.3) ** 2)
    )
    psychrometric_constant = _psychrometric_constant_kpa_per_c(elevation_m)
    extraterrestrial_radiation = _extraterrestrial_radiation_mj_m2_day(for_date, latitude)
    clear_sky_radiation = (0.75 + (2.0e-5 * elevation_m)) * extraterrestrial_radiation
    net_shortwave_radiation = (1.0 - 0.23) * solar_radiation_mj_m2
    relative_solar_ratio = (
        clamp(solar_radiation_mj_m2 / max(clear_sky_radiation, 0.0001), 0.0, 1.0)
        if clear_sky_radiation > 0
        else 0.0
    )
    net_longwave_radiation = 4.903e-9 * (
        (((temp_max_c + 273.16) ** 4) + ((temp_min_c + 273.16) ** 4)) / 2.0
    ) * (0.34 - (0.14 * sqrt(max(actual_vapor_pressure, 0.0)))) * (
        (1.35 * relative_solar_ratio) - 0.35
    )
    net_radiation = net_shortwave_radiation - net_longwave_radiation
    eto_mm_day = (
        (
            0.408 * slope_vapor_pressure_curve * net_radiation
            + psychrometric_constant
            * (900.0 / (temp_mean_c + 273.0))
            * wind_speed_m_s
            * max(0.0, saturation_vapor_pressure - actual_vapor_pressure)
        )
        / (
            slope_vapor_pressure_curve
            + psychrometric_constant * (1.0 + (0.34 * wind_speed_m_s))
        )
    )
    return round(max(0.0, eto_mm_day) / 25.4, 4)


def intraday_et_day_fraction(now_local: datetime) -> float:
    """Return the elapsed fraction of the local day."""

    midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    fraction = (now_local - midnight).total_seconds() / 86400.0
    return clamp(fraction, 0.0, 1.0)


def estimate_intraday_reference_et_inches(
    *,
    now_local: datetime,
    latitude: float,
    elevation_m: float,
    temperature_f: float,
    humidity_percent: float,
    wind_speed_mph: float,
    solar_radiation_wh_m2: float,
) -> float:
    """Return a display-only intraday ET estimate in inches."""

    day_fraction = intraday_et_day_fraction(now_local)
    if day_fraction <= 0.0:
        return 0.0
    estimated_full_day_solar_wh_m2 = max(0.0, float(solar_radiation_wh_m2)) / max(
        day_fraction,
        0.0001,
    )
    estimated_full_day_et_inches = calc_fao56_daily_reference_et_inches(
        for_date=now_local.date(),
        latitude=latitude,
        elevation_m=elevation_m,
        temperature_min_f=temperature_f,
        temperature_max_f=temperature_f,
        humidity_min_percent=humidity_percent,
        humidity_max_percent=humidity_percent,
        wind_speed_mph=wind_speed_mph,
        solar_radiation_wh_m2=estimated_full_day_solar_wh_m2,
    )
    return round(estimated_full_day_et_inches * day_fraction, 4)


def fallback_monthly_solar_wh_m2(for_date: date) -> float:
    """Return fallback monthly solar-radiation climatology in Wh/m²."""

    return round(
        MONTHLY_SOLAR_RADIATION_CLIMATOLOGY_MJ_M2.get(for_date.month, 18.0)
        / WATT_HOURS_M2_TO_MJ_M2,
        2,
    )


def _time_from_hour_float(hour_float: float) -> dt_time:
    """Convert a fractional hour into a wrapped local time."""

    normalized_hours = hour_float % 24.0
    total_minutes = int(round(normalized_hours * 60)) % (24 * 60)
    hour = total_minutes // 60
    minute = total_minutes % 60
    return dt_time(hour=hour, minute=minute)


def _weekday_key(for_date: date) -> str:
    """Return the normalized weekday key for a date."""

    return WEEKDAY_KEYS[for_date.weekday()]


def _weekday_label(weekday_key: str) -> str:
    """Return a display label for a weekday key."""

    return WEEKDAY_LABELS.get(weekday_key, weekday_key.title())


def _controller_day_runtime_key(device_id: str, weekday_key: str) -> str:
    """Return the runtime-data key for a controller weekday restriction."""

    return f"{device_id}:{weekday_key}"


def resolve_zone_agronomy(
    *,
    device_id: str,
    zone_number: int,
    zone_watering_profiles: Mapping[str, str],
    zone_root_depths: Mapping[str, float],
    zone_soil_whc: Mapping[str, float],
    zone_mad_values: Mapping[str, float],
    zone_kc_values: Mapping[str, float],
    zone_trigger_buffers: Mapping[str, float],
) -> dict[str, float | str]:
    """Return explicit agronomy values for a zone."""

    runtime_key = f"{device_id}:{zone_number}"
    profile = normalize_zone_watering_profile(
        zone_watering_profiles.get(runtime_key, ZONE_WATERING_PROFILE_DEFAULT)
    )
    defaults = agronomy_defaults_for_profile(profile)
    root_depth_in = float(zone_root_depths.get(runtime_key, defaults["root_depth_in"]))
    soil_whc_in_per_in = float(
        zone_soil_whc.get(runtime_key, defaults["soil_whc_in_per_in"])
    )
    mad = float(zone_mad_values.get(runtime_key, defaults["mad"]))
    kc = float(zone_kc_values.get(runtime_key, defaults["kc"]))
    trigger_buffer_in = float(
        zone_trigger_buffers.get(runtime_key, DEFAULT_ZONE_TRIGGER_BUFFER_INCHES)
    )
    capacity_in = compute_capacity_inches(root_depth_in, soil_whc_in_per_in, mad)
    return {
        "runtime_key": runtime_key,
        "profile": profile,
        "root_depth_in": round(root_depth_in, 2),
        "soil_whc_in_per_in": round(soil_whc_in_per_in, 3),
        "mad": round(mad, 3),
        "kc": round(kc, 3),
        "trigger_buffer_in": round(
            clamp(
                trigger_buffer_in,
                MIN_ZONE_TRIGGER_BUFFER_INCHES,
                MAX_ZONE_TRIGGER_BUFFER_INCHES,
            ),
            3,
        ),
        "capacity_in": capacity_in,
    }


def _zone_day_runtime_key(device_id: str, zone_number: int, weekday_key: str) -> str:
    """Return the runtime-data key for a zone weekday restriction."""

    return f"{device_id}:{zone_number}:{weekday_key}"


def _controller_day_restriction(
    device_id: str,
    weekday_key: str,
    controller_day_restrictions: Mapping[str, str],
) -> str:
    """Return the normalized controller-level weekday restriction."""

    return normalize_day_restriction(
        controller_day_restrictions.get(
            _controller_day_runtime_key(device_id, weekday_key)
        )
    )


def _zone_day_restriction(
    device_id: str,
    zone_number: int,
    weekday_key: str,
    zone_day_restrictions: Mapping[str, str],
) -> str:
    """Return the normalized zone-level weekday restriction."""

    del device_id, zone_number, weekday_key, zone_day_restrictions
    return DAY_RESTRICTION_AUTO


def _zone_allowed_on_weekday(
    device_id: str,
    zone_number: int,
    weekday_key: str,
    controller_day_restrictions: Mapping[str, str],
    zone_day_restrictions: Mapping[str, str],
) -> bool:
    """Return whether the zone may water on the supplied weekday."""

    if (
        _controller_day_restriction(
            device_id,
            weekday_key,
            controller_day_restrictions,
        )
        == DAY_RESTRICTION_DISABLED
    ):
        return False
    return True


def _allowed_watering_days_per_week(
    controller: BhyveSprinklerControllerSnapshot,
    zone_watering_profiles: Mapping[str, str],
    controller_day_restrictions: Mapping[str, str],
    zone_day_restrictions: Mapping[str, str],
) -> int:
    """Return the allowed-days-per-week count from controller-global restrictions."""

    del zone_day_restrictions
    active_zone_exists = any(
        zone.enabled
        and normalize_zone_watering_profile(
            zone_watering_profiles.get(
                f"{controller.device_id}:{zone.zone_number}",
                ZONE_WATERING_PROFILE_DEFAULT,
            )
        )
        != ZONE_WATERING_PROFILE_DISABLED
        for zone in controller.zones
    )
    if not active_zone_exists:
        return 7
    allowed_days = sum(
        1
        for weekday_key in WEEKDAY_KEYS
        if _controller_day_restriction(
            controller.device_id,
            weekday_key,
            controller_day_restrictions,
        )
        != DAY_RESTRICTION_DISABLED
    )
    return max(1, allowed_days)


def _next_allowed_schedule_offset_days(
    *,
    controller: BhyveSprinklerControllerSnapshot,
    start_date: date,
    start_offset_days: int,
    zone_watering_profiles: Mapping[str, str],
    controller_day_restrictions: Mapping[str, str],
    zone_day_restrictions: Mapping[str, str],
) -> int:
    """Return days until the next allowed controller watering day."""

    for offset in range(max(0, start_offset_days), max(0, start_offset_days) + 14):
        check_date = start_date + timedelta(days=offset)
        weekday_key = _weekday_key(check_date)
        if _controller_day_restriction(
            controller.device_id,
            weekday_key,
            controller_day_restrictions,
        ) == DAY_RESTRICTION_DISABLED:
            continue
        for zone in controller.zones:
            runtime_key = f"{controller.device_id}:{zone.zone_number}"
            watering_profile = normalize_zone_watering_profile(
                zone_watering_profiles.get(runtime_key, ZONE_WATERING_PROFILE_DEFAULT)
            )
            if zone.enabled and watering_profile != ZONE_WATERING_PROFILE_DISABLED:
                return offset
    return max(1, start_offset_days)


def _weekday_restriction_reason(
    *,
    weekday_label: str,
    controller_day_restriction: str,
    zone_day_restriction: str,
) -> str:
    """Return a human-friendly reason for a weekday restriction hold."""

    del zone_day_restriction
    if controller_day_restriction == DAY_RESTRICTION_DISABLED:
        return (
            f"{weekday_label} is disabled by the controller watering-day schedule. "
            "Today's runtime will stay banked until the next allowed day."
        )
    return (
        f"{weekday_label} is currently restricted by the watering-day schedule. "
        "Today's runtime will stay banked until the next allowed day."
    )


def monthly_reference(for_date: date, latitude: float) -> Mapping[str, float]:
    """Return the seasonal ET reference, flipped for the southern hemisphere."""

    month = for_date.month
    if latitude < 0:
        month = ((month + 5) % 12) + 1
    return MONTHLY_ET_REFERENCE[month]


def _solar_terms(for_date: date, latitude: float) -> tuple[float, float]:
    """Return extraterrestrial radiation and daylight hours for the date/location."""

    day_of_year = for_date.timetuple().tm_yday
    latitude_radians = clamp(float(latitude), -89.8, 89.8) * pi / 180.0
    inverse_relative_distance = 1 + 0.033 * cos((2 * pi / 365.0) * day_of_year)
    solar_declination = 0.409 * sin((2 * pi / 365.0) * day_of_year - 1.39)
    sunset_term = -tan(latitude_radians) * tan(solar_declination)
    sunset_angle = acos(clamp(sunset_term, -1.0, 1.0))
    radiation = (
        (24 * 60 / pi)
        * _SOLAR_CONSTANT_MJ_M2_MIN
        * inverse_relative_distance
        * (
            sunset_angle * sin(latitude_radians) * sin(solar_declination)
            + cos(latitude_radians) * cos(solar_declination) * sin(sunset_angle)
        )
    )
    daylight_hours = 24 / pi * sunset_angle
    return radiation, daylight_hours


def _radiation_factor(for_date: date, latitude: float) -> float:
    """Return the relative radiation factor vs. the Utah reference latitude."""

    local_radiation, _ = _solar_terms(for_date, latitude)
    reference_radiation, _ = _solar_terms(for_date, REFERENCE_LATITUDE)
    if reference_radiation <= 0:
        return 1.0
    return clamp(local_radiation / reference_radiation, 0.65, 1.35)


def calc_effective_rain(
    raw_rain_inches: float | None,
    rain_active_hours: float | None = None,
) -> float:
    """Discount rainfall that either evaporates quickly or runs off."""

    def _smooth_segment(
        value: float,
        start_x: float,
        end_x: float,
        start_y: float,
        end_y: float,
    ) -> float:
        """Interpolate smoothly between two anchor points."""

        span = max(end_x - start_x, 1e-9)
        t = max(0.0, min(1.0, (value - start_x) / span))
        smooth_t = t * t * (3.0 - 2.0 * t)
        return start_y + (end_y - start_y) * smooth_t

    raw_rain = max(0.0, float(raw_rain_inches or 0.0))
    if raw_rain <= 0:
        return 0.0
    # Use a continuous anchored curve instead of hard efficiency bands so
    # small source revisions do not create visible step changes in the
    # effective-rain and deficit charts, while preserving the intended
    # planner behavior that frequent light rain usually helps more than
    # a single larger downpour.
    if raw_rain < 0.25:
        effective_rain = raw_rain * 0.80
    elif raw_rain < 0.50:
        effective_rain = _smooth_segment(raw_rain, 0.25, 0.50, 0.20, 0.45)
    elif raw_rain < 0.75:
        effective_rain = _smooth_segment(raw_rain, 0.50, 0.75, 0.45, 0.50)
    elif raw_rain < 1.50:
        effective_rain = _smooth_segment(raw_rain, 0.75, 1.50, 0.50, 0.60)
    else:
        effective_rain = 0.60
    if rain_active_hours is not None and rain_active_hours > 0:
        average_rate = raw_rain / max(float(rain_active_hours), _EFFECTIVE_RAIN_MIN_ACTIVE_HOURS)
        rate_ratio = max(average_rate, 0.001) / _EFFECTIVE_RAIN_REFERENCE_RATE_IN_PER_HOUR
        timing_factor = clamp(
            rate_ratio ** (-_EFFECTIVE_RAIN_RATE_EXPONENT),
            _EFFECTIVE_RAIN_RATE_MIN_FACTOR,
            _EFFECTIVE_RAIN_RATE_MAX_FACTOR,
        )
        effective_rain *= timing_factor

    return round(min(max(effective_rain, 0.0), 0.60), 3)


def normalize_probability(value: float | None) -> float | None:
    """Normalize a rain-probability reading to a 0-100 scale."""

    if value is None:
        return None
    probability = float(value)
    if probability <= 1.0:
        probability *= 100.0
    return clamp(probability, 0.0, 100.0)


def calc_daily_et_inches(
    for_date: date,
    latitude: float,
    temperature_f: float | None,
    uv_index: float | None,
    humidity_percent: float | None = None,
    wind_speed_mph: float | None = None,
    irradiance_w_m2: float | None = None,
) -> tuple[float, float]:
    """Return estimated ET inches plus the seasonal multiplier used."""

    reference = monthly_reference(for_date, latitude)
    base_daily_et = float(reference["daily_et_inches"])
    reference_temp = float(reference["reference_temp_f"])
    radiation_factor = _radiation_factor(for_date, latitude)
    et_multiplier = round(float(reference["et_multiplier"]) * radiation_factor, 3)

    temp_factor = 1.0
    if temperature_f is not None:
        temp_factor = clamp(1.0 + ((temperature_f - reference_temp) / 40.0), 0.70, 1.35)

    radiation_input_factor = 1.0
    if irradiance_w_m2 is not None:
        radiation_input_factor = clamp(
            0.70 + (min(max(irradiance_w_m2, 0.0), 1000.0) / 1000.0) * 0.35,
            0.70,
            1.15,
        )
    elif uv_index is not None:
        radiation_input_factor = clamp(0.85 + (uv_index / 10.0) * 0.15, 0.85, 1.20)

    humidity_factor = 1.0
    if humidity_percent is not None:
        humidity_factor = clamp(
            1.12 - ((humidity_percent - 30.0) / 50.0) * 0.20,
            0.88,
            1.15,
        )

    wind_factor = 1.0
    if wind_speed_mph is not None:
        wind_factor = clamp(
            0.95 + (min(max(wind_speed_mph, 0.0), 25.0) / 20.0) * 0.15,
            0.95,
            1.15,
        )

    return (
        round(
            base_daily_et
            * radiation_factor
            * temp_factor
            * radiation_input_factor
            * humidity_factor
            * wind_factor,
            3,
        ),
        et_multiplier,
    )


def _solar_anchor_hours(
    for_date: date,
    latitude: float,
    longitude: float,
    utc_offset_hours: float,
) -> tuple[float, float]:
    """Return local sunrise and sunset hours for the given date/location."""

    _, daylight_hours = _solar_terms(for_date, latitude)
    solar_noon = 12 + (((utc_offset_hours * 15.0) - longitude) / 15.0)
    sunrise_hour = solar_noon - (daylight_hours / 2.0)
    sunset_hour = solar_noon + (daylight_hours / 2.0)
    return sunrise_hour, sunset_hour


def calc_daily_et_progress_fraction(
    for_datetime: datetime,
    latitude: float,
    longitude: float,
) -> float:
    """Return the accumulated share of today's ET at the given local time."""

    utc_offset_hours = (for_datetime.utcoffset() or timedelta()).total_seconds() / 3600.0
    sunrise_hour, sunset_hour = _solar_anchor_hours(
        for_datetime.date(),
        latitude,
        longitude,
        utc_offset_hours,
    )
    current_hour = (
        for_datetime.hour
        + (for_datetime.minute / 60.0)
        + (for_datetime.second / 3600.0)
    )
    if current_hour <= sunrise_hour:
        return 0.0
    if current_hour >= sunset_hour:
        return 1.0

    daylight_span = max(sunset_hour - sunrise_hour, 1e-9)
    daylight_progress = clamp(
        (current_hour - sunrise_hour) / daylight_span,
        0.0,
        1.0,
    )
    # Approximate accumulated daylight-driven ET as the integral of a simple
    # sine-shaped daytime demand curve so it rises through the day and then
    # flattens instead of dropping again after sunset.
    return round((1.0 - cos(pi * daylight_progress)) / 2.0, 4)


def calc_accumulated_daily_et_inches(
    for_datetime: datetime,
    latitude: float,
    longitude: float,
    temperature_f: float | None,
    uv_index: float | None,
    humidity_percent: float | None = None,
    wind_speed_mph: float | None = None,
    irradiance_w_m2: float | None = None,
) -> tuple[float, float, float, float]:
    """Return accumulated ET, full-day ET, seasonal multiplier, and progress."""

    full_day_et_inches, et_multiplier = calc_daily_et_inches(
        for_datetime.date(),
        latitude,
        temperature_f,
        uv_index,
        humidity_percent,
        wind_speed_mph,
        irradiance_w_m2,
    )
    progress_fraction = calc_daily_et_progress_fraction(
        for_datetime,
        latitude,
        longitude,
    )
    accumulated_et_inches = round(full_day_et_inches * progress_fraction, 3)
    return accumulated_et_inches, full_day_et_inches, et_multiplier, progress_fraction


def calc_weekly_target_inches(for_date: date, latitude: float) -> float:
    """Return a location-adjusted weekly water target."""

    reference = monthly_reference(for_date, latitude)
    return round(float(reference["weekly_target_inches"]) * _radiation_factor(for_date, latitude), 3)


def max_session_minutes(sprinkler_head_type: str | None) -> int:
    """Return the default single-session cap for the zone's sprinkler head type."""

    normalized = resolve_zone_wind_profile(sprinkler_head_type)
    if normalized == SPRINKLER_WIND_PROFILE_STANDARD_SPRAY:
        return 30
    if normalized == SPRINKLER_WIND_PROFILE_ROTARY_STREAM:
        return 45
    if normalized == SPRINKLER_WIND_PROFILE_DRIP_BUBBLER:
        return 90
    return DEFAULT_ZONE_MAX_SESSION_MINUTES


def cycle_and_soak_threshold_minutes(sprinkler_head_type: str | None) -> int:
    """Return the runtime where the zone should be split into multiple cycles."""

    normalized = resolve_zone_wind_profile(sprinkler_head_type)
    if normalized == SPRINKLER_WIND_PROFILE_STANDARD_SPRAY:
        return 20
    if normalized == SPRINKLER_WIND_PROFILE_ROTARY_STREAM:
        return 30
    if normalized == SPRINKLER_WIND_PROFILE_DRIP_BUBBLER:
        return 60
    return DEFAULT_CYCLE_AND_SOAK_THRESHOLD_MINUTES


def calc_zone_irrigation_inches(
    application_rate_inches_per_hour: float | None,
    runtime_minutes: float,
) -> float | None:
    """Estimate applied water depth from a measured application rate."""

    if application_rate_inches_per_hour is None or application_rate_inches_per_hour <= 0:
        return None
    inches_applied = float(application_rate_inches_per_hour) * (float(runtime_minutes) / 60.0)
    return round(max(0.0, inches_applied), 3)


def agronomy_defaults_for_profile(profile: str | None) -> dict[str, float]:
    """Return the explicit agronomy defaults for a watering profile."""

    normalized = normalize_zone_watering_profile(profile)
    return dict(
        ZONE_AGRONOMY_DEFAULTS.get(
            normalized,
            ZONE_AGRONOMY_DEFAULTS[ZONE_WATERING_PROFILE_DEFAULT],
        )
    )


def compute_capacity_inches(
    root_depth_in: float,
    soil_whc_in_per_in: float,
    mad: float,
) -> float:
    """Return the allowable-depletion bucket size for a zone."""

    return round(
        max(0.0, float(root_depth_in) * float(soil_whc_in_per_in) * float(mad)),
        3,
    )


def clamp_bucket_current_water(current_water_in: float, capacity_in: float) -> float:
    """Clamp current usable water into the allowable-depletion bucket."""

    return round(clamp(float(current_water_in), 0.0, max(0.0, float(capacity_in))), 3)


def derive_deficit_inches(current_water_in: float, capacity_in: float) -> float:
    """Derive deficit from the current bucket fill state."""

    return round(max(0.0, float(capacity_in) - float(current_water_in)), 3)


def bucket_fill_ratio(current_water_in: float, capacity_in: float) -> float:
    """Return the current usable-water fill ratio for a zone bucket."""

    if capacity_in <= 0:
        return 0.0
    return clamp(float(current_water_in) / float(capacity_in), 0.0, 1.0)


def zone_irrigation_event_key(event: BhyveLatestEvent) -> str | None:
    """Return a stable key for a completed irrigation event."""

    if event.end_ts is None:
        return None
    schedule_name = event.schedule_name or ""
    schedule_type = event.schedule_type or ""
    return (
        f"{event.end_ts}:{event.duration or 0}:{schedule_name}:{schedule_type}"
    )


def daylight_gate_hours() -> tuple[int, int]:
    """Return the shared daylight ET gate hours."""

    return ET_DAYLIGHT_START_HOUR, ET_DAYLIGHT_END_HOUR


def count_daylight_hours_in_window(
    *,
    start: datetime,
    end: datetime,
    daylight_start_hour: int | None = None,
    daylight_end_hour: int | None = None,
) -> float:
    """Return daylight-only hours between two local datetimes."""

    if end <= start:
        return 0.0

    daylight_start = ET_DAYLIGHT_START_HOUR if daylight_start_hour is None else daylight_start_hour
    daylight_end = ET_DAYLIGHT_END_HOUR if daylight_end_hour is None else daylight_end_hour
    total_hours = 0.0
    cursor = start
    while cursor < end:
        day_start = datetime.combine(cursor.date(), dt_time.min, tzinfo=cursor.tzinfo)
        next_day = day_start + timedelta(days=1)
        segment_end = min(end, next_day)
        daylight_segment_start = day_start + timedelta(hours=daylight_start)
        daylight_segment_end = day_start + timedelta(hours=daylight_end)
        overlap_start = max(cursor, daylight_segment_start)
        overlap_end = min(segment_end, daylight_segment_end)
        if overlap_end > overlap_start:
            total_hours += (overlap_end - overlap_start).total_seconds() / 3600.0
        cursor = segment_end

    return round(total_hours, 2)


def project_et_draw(
    hourly_et_in: float,
    now_local: datetime,
    next_window_start: datetime,
) -> tuple[float, float]:
    """Project ET draw between now and the next allowed window over daylight only."""

    daylight_start, daylight_end = daylight_gate_hours()
    daylight_hours = count_daylight_hours_in_window(
        start=now_local,
        end=next_window_start,
        daylight_start_hour=daylight_start,
        daylight_end_hour=daylight_end,
    )
    projected_draw = round(max(0.0, float(hourly_et_in)) * daylight_hours, 3)
    return projected_draw, daylight_hours


def daylight_hours_per_day() -> int:
    """Return the total daylight hours in the shared ET gate."""

    return max(0, ET_DAYLIGHT_END_HOUR - ET_DAYLIGHT_START_HOUR)


def zone_hourly_et_inches(
    *,
    hourly_et_inches: float,
    kc: float,
    exposure_factor: float,
    overall_watering_coefficient: float,
    zone_watering_coefficient: float,
) -> float:
    """Return the zone-specific hourly ET draw used by the bucket model."""

    multiplier = (
        max(0.0, float(kc))
        * max(0.1, float(overall_watering_coefficient))
        * clamp(
            float(zone_watering_coefficient),
            MIN_ZONE_WATERING_COEFFICIENT,
            MAX_ZONE_WATERING_COEFFICIENT,
        )
        * max(0.1, float(exposure_factor))
    )
    return round(max(0.0, float(hourly_et_inches)) * multiplier, 4)


def zone_daily_et_inches(zone_hourly_et: float) -> float:
    """Return the daylight-gated daily ET estimate for a zone."""

    return round(max(0.0, float(zone_hourly_et)) * daylight_hours_per_day(), 3)


def coerce_zone_bucket_state(
    state: BhyveZoneBucketState | Mapping[str, object] | None,
    *,
    capacity_inches: float,
) -> BhyveZoneBucketState | None:
    """Return a normalized bucket state when one is available."""

    if state is None:
        return None
    if isinstance(state, BhyveZoneBucketState):
        return BhyveZoneBucketState(
            capacity_inches=round(float(capacity_inches), 3),
            current_water_inches=clamp_bucket_current_water(
                state.current_water_inches,
                capacity_inches,
            ),
            last_bucket_update=state.last_bucket_update,
            last_et_hour_key=state.last_et_hour_key,
            last_authoritative_et_date=state.last_authoritative_et_date,
            last_effective_rain_date=state.last_effective_rain_date,
            last_effective_rain_total_inches=round(
                float(state.last_effective_rain_total_inches),
                3,
            ),
            last_irrigation_event_key=state.last_irrigation_event_key,
        )
    if not isinstance(state, Mapping):
        return None
    return BhyveZoneBucketState(
        capacity_inches=round(float(capacity_inches), 3),
        current_water_inches=clamp_bucket_current_water(
            float(state.get("current_water_inches", capacity_inches) or capacity_inches),
            capacity_inches,
        ),
        last_bucket_update=(
            str(state.get("last_bucket_update"))
            if state.get("last_bucket_update") is not None
            else None
        ),
        last_et_hour_key=(
            str(state.get("last_et_hour_key"))
            if state.get("last_et_hour_key") is not None
            else None
        ),
        last_authoritative_et_date=(
            str(state.get("last_authoritative_et_date"))
            if state.get("last_authoritative_et_date") is not None
            else None
        ),
        last_effective_rain_date=(
            str(state.get("last_effective_rain_date"))
            if state.get("last_effective_rain_date") is not None
            else None
        ),
        last_effective_rain_total_inches=round(
            float(state.get("last_effective_rain_total_inches", 0.0) or 0.0),
            3,
        ),
        last_irrigation_event_key=(
            str(state.get("last_irrigation_event_key"))
            if state.get("last_irrigation_event_key") is not None
            else None
        ),
    )


def migrate_bucket_capacity(
    bucket_state: BhyveZoneBucketState,
    *,
    capacity_inches: float,
) -> tuple[BhyveZoneBucketState, dict[str, float | bool]]:
    """Preserve fill ratio when a zone's bucket capacity changes."""

    old_capacity_inches = round(float(bucket_state.capacity_inches), 3)
    new_capacity_inches = round(float(capacity_inches), 3)
    old_water_inches = clamp_bucket_current_water(
        bucket_state.current_water_inches,
        max(old_capacity_inches, 0.0),
    )
    fill_ratio = bucket_fill_ratio(old_water_inches, max(old_capacity_inches, 0.0))
    if abs(old_capacity_inches - new_capacity_inches) <= 0.001:
        return bucket_state, {
            "changed": False,
            "old_capacity_inches": old_capacity_inches,
            "new_capacity_inches": new_capacity_inches,
            "old_water_inches": old_water_inches,
            "new_water_inches": old_water_inches,
            "fill_ratio": round(fill_ratio, 3),
            "clamped_to_full": False,
        }

    unclamped_new_water_inches = new_capacity_inches * fill_ratio
    new_water_inches = clamp_bucket_current_water(
        unclamped_new_water_inches,
        new_capacity_inches,
    )
    clamped_to_full = (
        old_water_inches > new_capacity_inches + 0.001
        and abs(new_water_inches - new_capacity_inches) <= 0.001
    )

    return (
        BhyveZoneBucketState(
            capacity_inches=new_capacity_inches,
            current_water_inches=new_water_inches,
            last_bucket_update=bucket_state.last_bucket_update,
            last_et_hour_key=bucket_state.last_et_hour_key,
            last_authoritative_et_date=bucket_state.last_authoritative_et_date,
            last_effective_rain_date=bucket_state.last_effective_rain_date,
            last_effective_rain_total_inches=bucket_state.last_effective_rain_total_inches,
            last_irrigation_event_key=bucket_state.last_irrigation_event_key,
        ),
        {
            "changed": True,
            "old_capacity_inches": old_capacity_inches,
            "new_capacity_inches": new_capacity_inches,
            "old_water_inches": old_water_inches,
            "new_water_inches": new_water_inches,
            "fill_ratio": round(fill_ratio, 3),
            "clamped_to_full": clamped_to_full,
        },
    )


def estimate_legacy_zone_deficit_inches(
    *,
    zone: BhyveSprinklerZone,
    since_utc: datetime,
    effective_rain_7d_inches: float,
    et_7d_inches: float,
    zone_application_rate_inches_per_hour: float | None,
    weekly_target_inches: float,
    overall_watering_coefficient: float,
    zone_watering_coefficient: float,
    kc: float,
) -> tuple[float, int, float, float, float]:
    """Estimate a legacy-style positive deficit for cold-start bucket bootstrap."""

    recent_minutes, recent_inches = calc_recent_zone_irrigation(
        zone,
        since_utc,
        zone_application_rate_inches_per_hour,
    )
    exposure_factor = _zone_exposure_factor(zone)
    zone_multiplier = zone_hourly_et_inches(
        hourly_et_inches=(et_7d_inches / max(1, WATER_BALANCE_WINDOW_DAYS * daylight_hours_per_day())),
        kc=kc,
        exposure_factor=exposure_factor,
        overall_watering_coefficient=overall_watering_coefficient,
        zone_watering_coefficient=zone_watering_coefficient,
    ) / max(0.0001, (et_7d_inches / max(1, WATER_BALANCE_WINDOW_DAYS * daylight_hours_per_day())) or 0.0001)
    projected_weekly_target = round(
        max(0.0, weekly_target_inches * zone_multiplier),
        3,
    )
    legacy_deficit = round(
        max(0.0, (et_7d_inches * zone_multiplier) - effective_rain_7d_inches - recent_inches),
        3,
    )
    return legacy_deficit, recent_minutes, recent_inches, projected_weekly_target, exposure_factor


def calc_zone_allowable_depletion_inches(zone: BhyveSprinklerZone) -> float | None:
    """Return how much water the root zone can lose before stress."""

    if zone.available_water_capacity is None or zone.manage_allow_depletion is None:
        return None

    root_depth = zone.manual_root_depth or zone.root_depth
    if root_depth in (None, 0):
        return None

    depletion = (
        float(zone.available_water_capacity)
        * float(root_depth)
        * (float(zone.manage_allow_depletion) / 100.0)
    )
    return round(max(0.0, depletion), 3)


def calc_recent_zone_irrigation(
    zone: BhyveSprinklerZone,
    since_utc: datetime,
    application_rate_inches_per_hour: float | None,
) -> tuple[int, float]:
    """Estimate recent irrigation minutes and inches from B-hyve event history."""

    total_minutes = 0
    total_inches = 0.0
    for event in merged_zone_recent_events(zone):
        if event.end_ts is None or event.duration is None:
            continue
        event_time = datetime.fromtimestamp(event.end_ts, tz=since_utc.tzinfo)
        if event_time < since_utc:
            continue

        runtime_minutes = max(0, round(event.duration / 60))
        total_minutes += runtime_minutes
        inches = calc_zone_irrigation_inches(
            application_rate_inches_per_hour,
            event.duration / 60,
        )
        if inches is not None:
            total_inches += inches

    return total_minutes, round(total_inches, 3)


def _collect_recent_controller_runs(
    controller: BhyveSprinklerControllerSnapshot,
    since_utc: datetime,
) -> tuple[BhyveControllerRecentRun, ...]:
    """Return recent watering events across all zones on the controller."""

    runs: list[BhyveControllerRecentRun] = []
    seen: set[tuple[int, int | None, int | None, str | None, str | None]] = set()
    for zone in controller.zones:
        for event in merged_zone_recent_events(zone):
            event_key = (
                zone.zone_number,
                event.end_ts,
                event.duration,
                event.schedule_name,
                event.schedule_type,
            )
            if event_key in seen:
                continue
            seen.add(event_key)

            if event.end_ts is None or event.duration is None:
                continue
            event_time = datetime.fromtimestamp(event.end_ts, tz=since_utc.tzinfo)
            if event_time < since_utc:
                continue

            runs.append(
                BhyveControllerRecentRun(
                    zone_number=zone.zone_number,
                    zone_name=zone.name,
                    duration_minutes=max(0, round(event.duration / 60)),
                    end_local=event.end_local,
                    end_ts=event.end_ts,
                    schedule_name=event.schedule_name,
                    schedule_type=event.schedule_type,
                )
            )

    runs.sort(key=lambda item: item.end_ts or 0, reverse=True)
    return tuple(runs)


def build_cycle_minutes(
    total_minutes: int,
    threshold_minutes: int = DEFAULT_CYCLE_AND_SOAK_THRESHOLD_MINUTES,
) -> tuple[int, ...]:
    """Return cycle-and-soak segments for a runtime recommendation."""

    if total_minutes <= 0:
        return ()
    if total_minutes <= threshold_minutes:
        return (total_minutes,)

    first = ceil(total_minutes / 2)
    second = max(0, total_minutes - first)
    if second == 0:
        return (first,)
    return (first, second)


def _window_duration_minutes(start_time: dt_time, end_time: dt_time) -> int:
    """Return the duration of a watering window in minutes."""

    start_dt = datetime.combine(date(2000, 1, 1), start_time)
    end_dt = datetime.combine(date(2000, 1, 1), end_time)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return max(0, round((end_dt - start_dt).total_seconds() / 60))


def _zone_window_priority(
    zone: BhyveSprinklerZone,
    *,
    now_local: datetime,
    vegetable_garden_mode: bool,
    water_efficient_mode: bool,
    trees_shrubs_mode: bool,
    last_run_age_days: float | None,
    recent_runtime_minutes_7d: int,
) -> tuple[float, int, int]:
    """Return the scheduling priority when demand exceeds the active window."""

    crop_type = (zone.crop_type or "").upper()
    if vegetable_garden_mode or crop_type == "GARDEN":
        crop_bonus = 1.0
    elif "GRASS" in crop_type:
        crop_bonus = 0.5
    elif trees_shrubs_mode:
        crop_bonus = -0.2
    elif water_efficient_mode:
        crop_bonus = -0.5
    else:
        crop_bonus = 0.0

    run_age = last_run_age_days if last_run_age_days is not None else 999.0
    rotated_zone_order = (zone.zone_number + now_local.toordinal()) % 64
    urgency_score = run_age + crop_bonus - (recent_runtime_minutes_7d / 120.0)
    return (
        -urgency_score,
        rotated_zone_order,
        zone.zone_number,
    )


def suggest_watering_window(
    *,
    zones: Iterable[BhyveSprinklerZone],
    for_date: date,
    latitude: float,
    longitude: float,
    utc_offset_hours: float,
    temperature_f: float | None,
    total_runtime_minutes: int,
    allowed_watering_days_per_week: int = 7,
    maximum_window_minutes: int = DEFAULT_MAX_AUTOMATIC_WATERING_WINDOW_MINUTES,
    timing_preference: str = DEFAULT_AUTOMATIC_WINDOW_PREFERENCE,
) -> tuple[dt_time, dt_time, str]:
    """Return suggested start and end times based on location, season, and crop mix."""

    _, daylight_hours = _solar_terms(for_date, latitude)
    solar_noon = 12 + (((utc_offset_hours * 15.0) - longitude) / 15.0)
    sunrise_hour = solar_noon - (daylight_hours / 2.0)
    sunset_hour = solar_noon + (daylight_hours / 2.0)
    timing_preference = normalize_automatic_window_preference(timing_preference)
    anchor_hour = sunrise_hour
    anchor_label = "dawn"
    reason_parts = [
        "automatic window is anchored to finish as close to dawn as practical"
    ]
    if timing_preference == AUTOMATIC_WINDOW_PREFERENCE_EVENING:
        anchor_hour = sunset_hour
        anchor_label = "sunset"
        reason_parts = [
            "automatic window is anchored to start as close to sunset as practical"
        ]

    zone_list = list(zones)
    hot_weather = temperature_f is not None and temperature_f >= 90
    very_hot_weather = temperature_f is not None and temperature_f >= 98

    allowed_watering_days_per_week = max(1, min(7, int(allowed_watering_days_per_week or 7)))
    desired_window_minutes = max(0, int(round(total_runtime_minutes)))
    if (
        allowed_watering_days_per_week < 7
        and desired_window_minutes > 0
    ):
        reason_parts.append(
            f"restricted watering days may require longer runs across {allowed_watering_days_per_week} allowed day(s) per week"
        )
    if timing_preference == AUTOMATIC_WINDOW_PREFERENCE_MORNING and any(
        (zone.crop_type or "").upper() == "GARDEN" for zone in zone_list
    ):
        reason_parts.append("garden zones still finish by dawn to keep watering in the coolest part of the morning")
    elif timing_preference == AUTOMATIC_WINDOW_PREFERENCE_MORNING and any(
        (zone.nozzle_type or "").upper() == "DRIP_LINE" for zone in zone_list
    ):
        reason_parts.append("drip zones finish by dawn while allowing a longer soak when needed")
    if timing_preference == AUTOMATIC_WINDOW_PREFERENCE_MORNING and any(
        (zone.exposure_type or "").upper() == "LOTS_OF_SUN" for zone in zone_list
    ):
        reason_parts.append("full-sun exposure keeps the automatic window in the earliest cooler hours")
    if timing_preference == AUTOMATIC_WINDOW_PREFERENCE_MORNING and (
        hot_weather or total_runtime_minutes >= 150
    ):
        reason_parts.append("higher summer demand extends farther back from dawn")
    if timing_preference == AUTOMATIC_WINDOW_PREFERENCE_EVENING and (
        hot_weather or very_hot_weather or total_runtime_minutes >= 150
    ):
        reason_parts.append("higher summer demand extends farther past sunset")

    maximum_window_minutes = int(
        clamp(
            maximum_window_minutes or DEFAULT_MAX_AUTOMATIC_WATERING_WINDOW_MINUTES,
            MIN_AUTOMATIC_WATERING_WINDOW_MINUTES,
            MAX_AUTOMATIC_WATERING_WINDOW_MINUTES,
        )
    )
    if desired_window_minutes > maximum_window_minutes:
        desired_window_minutes = maximum_window_minutes
        reason_parts.append(
            f"automatic window is capped at the configured {maximum_window_minutes}-minute maximum"
        )

    anchor_time = _time_from_hour_float(anchor_hour)
    if timing_preference == AUTOMATIC_WINDOW_PREFERENCE_EVENING:
        start_dt = datetime.combine(for_date, anchor_time)
        end_dt = start_dt + timedelta(minutes=desired_window_minutes)
        if end_dt.date() > for_date:
            reason_parts.append(
                "window crosses midnight so the full runtime can still start near sunset"
            )
    else:
        end_dt = datetime.combine(for_date, anchor_time)
        start_dt = end_dt - timedelta(minutes=desired_window_minutes)
        if start_dt.date() < for_date:
            reason_parts.append(
                f"window crosses midnight so the full runtime can still finish by {anchor_label}"
            )

    return start_dt.time(), end_dt.time(), "; ".join(reason_parts)


def is_within_watering_window(
    now_time: dt_time,
    start_time: dt_time | None,
    end_time: dt_time | None,
) -> bool:
    """Return True when the current time falls inside the configured window."""

    start = start_time or DEFAULT_WATERING_START_TIME
    end = end_time or DEFAULT_WATERING_END_TIME

    if start <= end:
        return start <= now_time <= end
    return now_time >= start or now_time <= end


def _combine_local(
    now_local: datetime,
    target_date: date,
    target_time: dt_time,
) -> datetime:
    """Combine a local date and time while preserving the current tzinfo."""

    return datetime.combine(target_date, target_time, tzinfo=now_local.tzinfo)


def compute_next_window_start(
    *,
    now_local: datetime,
    controller: BhyveSprinklerControllerSnapshot,
    allowed_start_time: dt_time,
    allowed_end_time: dt_time,
    zone_watering_profiles: Mapping[str, str],
    controller_day_restrictions: Mapping[str, str],
    zone_day_restrictions: Mapping[str, str],
) -> datetime:
    """Return the next allowed watering-window start based on the real schedule."""

    start_dt = _combine_local(now_local, now_local.date(), allowed_start_time)
    end_dt = _combine_local(now_local, now_local.date(), allowed_end_time)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    search_offset = 0
    if start_dt <= now_local < end_dt:
        search_offset = 1
    elif now_local > end_dt:
        search_offset = 1

    next_offset_days = _next_allowed_schedule_offset_days(
        controller=controller,
        start_date=now_local.date(),
        start_offset_days=search_offset,
        zone_watering_profiles=zone_watering_profiles,
        controller_day_restrictions=controller_day_restrictions,
        zone_day_restrictions=zone_day_restrictions,
    )
    target_date = now_local.date() + timedelta(days=next_offset_days)
    return _combine_local(now_local, target_date, allowed_start_time)


def compute_next_trigger_horizon(
    *,
    now_local: datetime,
    controller: BhyveSprinklerControllerSnapshot,
    latitude: float,
    longitude: float,
    automatic_window_enabled: bool,
    automatic_window_preference: str,
    effective_start_time: dt_time,
    effective_end_time: dt_time,
    zone_watering_profiles: Mapping[str, str],
    controller_day_restrictions: Mapping[str, str],
    zone_day_restrictions: Mapping[str, str],
) -> tuple[datetime, int]:
    """Return the next watering-trigger horizon from the actual allowed schedule."""

    start_offset_days = 0
    if automatic_window_enabled:
        sunrise_hour, sunset_hour = _solar_anchor_hours(
            now_local.date(),
            latitude,
            longitude,
            (now_local.utcoffset() or timedelta()).total_seconds() / 3600.0,
        )
        anchor_hour = sunset_hour
        if normalize_automatic_window_preference(automatic_window_preference) == AUTOMATIC_WINDOW_PREFERENCE_MORNING:
            anchor_hour = sunrise_hour
        anchor_time = _time_from_hour_float(anchor_hour)
        anchor_dt = _combine_local(now_local, now_local.date(), anchor_time)
        if anchor_dt <= now_local:
            start_offset_days = 1
        next_offset_days = _next_allowed_schedule_offset_days(
            controller=controller,
            start_date=now_local.date(),
            start_offset_days=start_offset_days,
            zone_watering_profiles=zone_watering_profiles,
            controller_day_restrictions=controller_day_restrictions,
            zone_day_restrictions=zone_day_restrictions,
        )
        target_date = now_local.date() + timedelta(days=next_offset_days)
        sunrise_hour, sunset_hour = _solar_anchor_hours(
            target_date,
            latitude,
            longitude,
            (now_local.utcoffset() or timedelta()).total_seconds() / 3600.0,
        )
        target_hour = sunset_hour
        if normalize_automatic_window_preference(automatic_window_preference) == AUTOMATIC_WINDOW_PREFERENCE_MORNING:
            target_hour = sunrise_hour
        return _combine_local(
            now_local,
            target_date,
            _time_from_hour_float(target_hour),
        ), next_offset_days

    start_dt = _combine_local(now_local, now_local.date(), effective_start_time)
    end_dt = _combine_local(now_local, now_local.date(), effective_end_time)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    if start_dt <= now_local < end_dt or now_local > end_dt:
        start_offset_days = 1
    next_offset_days = _next_allowed_schedule_offset_days(
        controller=controller,
        start_date=now_local.date(),
        start_offset_days=start_offset_days,
        zone_watering_profiles=zone_watering_profiles,
        controller_day_restrictions=controller_day_restrictions,
        zone_day_restrictions=zone_day_restrictions,
    )
    target_date = now_local.date() + timedelta(days=next_offset_days)
    return _combine_local(now_local, target_date, effective_start_time), next_offset_days


def _project_next_cycle(
    now_local: datetime,
    decision: str,
    decision_reason: str,
    effective_start_time: dt_time,
    effective_end_time: dt_time,
    rain_delay_days: int,
    next_allowed_schedule_offset_days: int | None = None,
) -> tuple[str | None, str | None, str, str]:
    """Return the next projected watering window for dashboarding."""

    start_dt = _combine_local(now_local, now_local.date(), effective_start_time)
    end_dt = _combine_local(now_local, now_local.date(), effective_end_time)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    if decision == "run":
        if now_local <= start_dt:
            return (
                start_dt.isoformat(),
                end_dt.isoformat(),
                "scheduled_today",
                decision_reason,
            )
        if now_local <= end_dt:
            return (
                start_dt.isoformat(),
                end_dt.isoformat(),
                "in_current_window",
                decision_reason,
            )
        next_offset = max(1, int(next_allowed_schedule_offset_days or 1))
        next_start = start_dt + timedelta(days=next_offset)
        next_end = end_dt + timedelta(days=next_offset)
        return (
            next_start.isoformat(),
            next_end.isoformat(),
            "next_daily_window",
            "Today's watering window has passed; the next candidate cycle is the next allowed watering day.",
        )

    if decision == "rain_delay":
        delay_days = max(
            1,
            int(next_allowed_schedule_offset_days or rain_delay_days or 1),
        )
        next_start = start_dt + timedelta(days=delay_days)
        next_end = end_dt + timedelta(days=delay_days)
        return (
            next_start.isoformat(),
            next_end.isoformat(),
            "rain_delay",
            decision_reason,
        )

    if decision == "defer":
        next_offset = max(1, int(next_allowed_schedule_offset_days or 1))
        next_start = start_dt + timedelta(days=next_offset)
        next_end = end_dt + timedelta(days=next_offset)
        return (
            next_start.isoformat(),
            next_end.isoformat(),
            "forecast_hold",
            decision_reason,
        )

    if decision == "restricted_day":
        next_offset = max(1, int(next_allowed_schedule_offset_days or 1))
        next_start = start_dt + timedelta(days=next_offset)
        next_end = end_dt + timedelta(days=next_offset)
        return (
            next_start.isoformat(),
            next_end.isoformat(),
            "restricted_day",
            decision_reason,
        )

    next_offset = max(1, int(next_allowed_schedule_offset_days or 1))
    next_start = start_dt + timedelta(days=next_offset)
    next_end = end_dt + timedelta(days=next_offset)
    return (
        next_start.isoformat(),
        next_end.isoformat(),
        "monitor_only",
        "No watering cycle is recommended right now; the next candidate window is the next allowed watering day.",
    )


def _rain_delay_days(records: Iterable[BhyveDailyWaterBalance], today: date) -> int:
    """Return a derived rain-delay window based on recent effective rainfall."""

    keyed = {record.date: record for record in records}
    for days_back in range(0, 3):
        check_date = (today - timedelta(days=days_back)).isoformat()
        record = keyed.get(check_date)
        if record is None:
            continue
        rain = record.effective_rain_inches
        if rain >= 0.75:
            return max(0, 3 - days_back)
        if rain >= 0.50:
            return max(0, 2 - days_back)
        if rain >= 0.30 and days_back == 0:
            return 1
    return 0


def _dry_days_streak(records: Iterable[BhyveDailyWaterBalance], today: date) -> int:
    """Return consecutive dry-demand days ending today."""

    keyed = {record.date: record for record in records}
    streak = 0
    for days_back in range(0, WATER_BALANCE_WINDOW_DAYS):
        check_date = (today - timedelta(days=days_back)).isoformat()
        record = keyed.get(check_date)
        if record is None:
            break
        if record.effective_rain_inches > 0.05 or record.et_inches <= 0.05:
            break
        streak += 1
    return streak


def _zone_crop_coefficient(zone: BhyveSprinklerZone) -> float:
    """Return the active crop coefficient for a zone."""

    if zone.manual_crop_coefficient not in (None, 0):
        return float(zone.manual_crop_coefficient)
    if zone.crop_coefficient is not None:
        return float(zone.crop_coefficient)
    return 1.0


def _zone_exposure_factor(zone: BhyveSprinklerZone) -> float:
    """Return a simple exposure multiplier for the zone."""

    exposure = (zone.exposure_type or "").upper()
    if exposure == "LOTS_OF_SUN":
        return 1.08
    if exposure == "SOME_SHADE":
        return 0.88
    if exposure == "MOSTLY_SHADE":
        return 0.72
    return 1.0


def _zone_soil_storage_factor(
    zone: BhyveSprinklerZone,
    zone_weekly_target_inches: float,
) -> tuple[float, float | None]:
    """Return a storage-based frequency factor and estimated storage days."""

    soil_type = (zone.soil_type or "").upper()
    root_depth = float(zone.manual_root_depth or zone.root_depth or 0.0)
    texture_factor = 1.0
    if "SAND" in soil_type:
        texture_factor = 0.88
    elif "CLAY" in soil_type:
        texture_factor = 1.12
    elif "LOAM" in soil_type:
        texture_factor = 1.0

    depth_factor = 1.0
    if root_depth >= 10.0:
        depth_factor = 1.05
    elif 0.0 < root_depth <= 5.0:
        depth_factor = 0.92

    allowable_depletion = calc_zone_allowable_depletion_inches(zone)
    if allowable_depletion not in (None, 0) and zone_weekly_target_inches > 0:
        daily_target_inches = max(zone_weekly_target_inches / 7.0, 0.01)
        storage_days = round(allowable_depletion / daily_target_inches, 2)
        depletion_factor = clamp(
            0.88 + (min(storage_days, 7.0) / 7.0) * 0.22,
            0.88,
            1.10,
        )
        storage_factor = clamp(
            texture_factor * depth_factor * depletion_factor,
            0.75,
            1.35,
        )
        return round(storage_factor, 3), storage_days

    storage_factor = texture_factor * depth_factor

    return round(clamp(storage_factor, 0.75, 1.35), 3), None


def _weather_hold_reason(
    *,
    persisted_reason: str | None = None,
    temperature_f: float | None,
    min_watering_temperature_f: float,
    wind_speed_mph: float | None,
    max_watering_wind_speed_mph: float | None,
    wind_gust_mph: float | None,
    max_watering_gust_speed_mph: float | None,
) -> tuple[str, bool]:
    """Return an active weather-hold reason when watering should pause."""

    if persisted_reason:
        return persisted_reason, True

    reasons: list[str] = []
    if temperature_f is not None and temperature_f <= min_watering_temperature_f:
        reasons.append(
            f"the current temperature is {temperature_f:.1f} F and the minimum "
            f"watering temperature is {min_watering_temperature_f:.1f} F"
        )
    if (
        wind_speed_mph is not None
        and max_watering_wind_speed_mph is not None
        and wind_speed_mph >= max_watering_wind_speed_mph
    ):
        reasons.append(
            f"the current wind speed is {wind_speed_mph:.1f} mph and the maximum "
            f"watering wind speed is {max_watering_wind_speed_mph:.1f} mph"
        )
    elif (
        wind_gust_mph is not None
        and max_watering_gust_speed_mph is not None
        and wind_gust_mph >= max_watering_gust_speed_mph
    ):
        reasons.append(
            f"the current wind gust is {wind_gust_mph:.1f} mph and the gust "
            f"watering stop speed is {max_watering_gust_speed_mph:.1f} mph"
        )

    if not reasons:
        return "", False

    if len(reasons) == 1:
        return f"Watering is paused because {reasons[0]}.", True

    return (
        f"Watering is paused because {reasons[0]} and {reasons[1]}.",
        True,
    )


def _zone_last_run_age_days(
    zone: BhyveSprinklerZone,
    now_local: datetime,
    *,
    minimum_duration_minutes: int = _PROFILE_INTERVAL_MIN_EVENT_MINUTES,
) -> float | None:
    """Return the age of the most recent known watering event in days."""

    end_ts: int | None = None

    for event in merged_zone_recent_events(zone):
        if event.end_ts is None:
            continue
        if event.duration is not None and event.duration < (minimum_duration_minutes * 60):
            continue
        end_ts = event.end_ts
        break

    if end_ts is None:
        return None

    last_run = datetime.fromtimestamp(end_ts, tz=now_local.tzinfo)
    age_days = (now_local - last_run).total_seconds() / 86400.0
    return max(0.0, round(age_days, 2))


def _water_efficient_interval_days(
    zone: BhyveSprinklerZone,
    for_date: date,
    temperature_f: float | None,
) -> int:
    """Return the target watering interval for a low-water-use zone."""

    interval_days = _WATER_EFFICIENT_INTERVAL_DAYS[for_date.month]
    if (zone.nozzle_type or "").upper() == "DRIP_LINE":
        interval_days = max(4, interval_days)
    if temperature_f is not None and temperature_f < 60:
        interval_days = max(interval_days, 7)
    return interval_days


def _trees_shrubs_interval_days(
    zone: BhyveSprinklerZone,
    for_date: date,
    temperature_f: float | None,
) -> int:
    """Return the target watering interval for established trees and shrubs."""

    interval_days = _TREES_SHRUBS_INTERVAL_DAYS[for_date.month]
    if (zone.nozzle_type or "").upper() == "DRIP_LINE":
        interval_days = max(3, interval_days)
    if temperature_f is not None and temperature_f < 58:
        interval_days = max(interval_days, 6)
    return interval_days


def _vegetable_garden_interval_days(
    for_date: date,
    temperature_f: float | None,
) -> int:
    """Return the target irrigation interval for raised-bed vegetables."""

    interval_days = _VEGETABLE_GARDEN_INTERVAL_DAYS[for_date.month]
    if temperature_f is not None and temperature_f < 55:
        interval_days = max(interval_days, 3)
    return interval_days


def _vegetable_garden_session_cap(
    zone: BhyveSprinklerZone,
    for_date: date,
    temperature_f: float | None,
) -> int:
    """Return a short-session cap for frequent raised-bed irrigation."""

    del zone
    session_cap = _VEGETABLE_GARDEN_SESSION_CAP[for_date.month]
    if temperature_f is not None and temperature_f >= 100:
        session_cap = min(20, session_cap + 2)
    return session_cap


def _zone_profile_deficit_factor(watering_profile: str) -> float:
    """Return how aggressively a watering profile should accumulate deficit."""

    if watering_profile == ZONE_WATERING_PROFILE_DROUGHT_TOLERANT:
        return 0.72
    if watering_profile == ZONE_WATERING_PROFILE_TREES_SHRUBS:
        return 0.86
    if watering_profile == ZONE_WATERING_PROFILE_VEGETABLE_GARDEN:
        return 1.18
    return 1.0


def resolve_zone_wind_profile(configured_profile: str | None) -> str:
    """Return the effective wind profile for a single zone."""

    if configured_profile == SPRINKLER_WIND_PROFILE_ROTARY_STREAM:
        return SPRINKLER_WIND_PROFILE_ROTARY_STREAM
    if configured_profile == SPRINKLER_WIND_PROFILE_DRIP_BUBBLER:
        return SPRINKLER_WIND_PROFILE_DRIP_BUBBLER
    return SPRINKLER_WIND_PROFILE_STANDARD_SPRAY


def calc_wind_stop_thresholds(
    base_max_wind_speed_mph: float,
    effective_wind_profile: str,
) -> tuple[float | None, float | None]:
    """Return effective average/gust watering stop thresholds."""

    if effective_wind_profile == SPRINKLER_WIND_PROFILE_DRIP_BUBBLER:
        return None, None

    effective_max_wind = float(base_max_wind_speed_mph)
    if effective_wind_profile == SPRINKLER_WIND_PROFILE_ROTARY_STREAM:
        effective_max_wind = clamp(
            effective_max_wind + ROTARY_WIND_TOLERANCE_BONUS_MPH,
            0.0,
            MAX_WATERING_WIND_SPEED_MPH,
        )
        gust_threshold = max(
            effective_max_wind + ROTARY_GUST_THRESHOLD_OFFSET_MPH,
            effective_max_wind * ROTARY_GUST_THRESHOLD_FACTOR,
        )
    else:
        gust_threshold = max(
            effective_max_wind + SPRAY_GUST_THRESHOLD_OFFSET_MPH,
            effective_max_wind * SPRAY_GUST_THRESHOLD_FACTOR,
        )

    gust_threshold = clamp(gust_threshold, 0.0, MAX_WATERING_GUST_SPEED_MPH)
    return round(effective_max_wind, 2), round(gust_threshold, 2)


def _zone_seasonal_factor(
    zone: BhyveSprinklerZone,
    for_date: date,
    temperature_f: float | None,
) -> float:
    """Return a crop-specific seasonal factor based on calendar season.

    Live weather already feeds the ET side of the water-balance math. Keep the
    zone seasonal factor stable through the day so a cool evening or humid
    morning does not retroactively make the previous several days look less
    thirsty.
    """

    crop_type = (zone.crop_type or "").upper()
    month = for_date.month
    if crop_type == "COOL_SEASON_GRASS":
        factor = _COOL_SEASON_GRASS_FACTORS[month]
    elif crop_type == "WARM_SEASON_GRASS":
        factor = _WARM_SEASON_GRASS_FACTORS[month]
    elif crop_type == "PERENNIALS":
        factor = _PERENNIAL_FACTORS[month]
    elif crop_type == "GARDEN":
        factor = _GARDEN_FACTORS[month]
    else:
        factor = 1.0

    del temperature_f

    return round(clamp(factor, 0.10, 1.35), 3)


def _max_scale_factor(for_date: date, temperature_f: float | None) -> float:
    """Return a seasonal scaling ceiling for the current planning window."""

    if for_date.month in {3, 4, 5, 9, 10}:
        return 1.25
    if temperature_f is not None and temperature_f >= 95 and for_date.month in {6, 7, 8}:
        return 1.75
    if for_date.month in {6, 7, 8}:
        return 1.60
    return 1.50


def build_controller_plan(
    *,
    controller: BhyveSprinklerControllerSnapshot,
    now_local: datetime,
    daily_records: Iterable[BhyveDailyWaterBalance],
    daily_rain_inches: float,
    rain_active_hours_24h: float | None,
    latitude: float,
    longitude: float,
    location_source: str,
    temperature_f: float | None,
    uv_index: float | None,
    irradiance_w_m2: float | None,
    humidity_percent: float | None,
    wind_speed_mph: float | None,
    wind_gust_mph: float | None,
    forecast_rain_amount_inches: float | None,
    forecast_rain_probability: float | None,
    overall_watering_coefficient: float,
    minimum_run_threshold_minutes: int,
    max_watering_wind_speed_mph: float,
    min_watering_temperature_f: float,
    zone_application_rates: Mapping[str, float],
    max_weekly_runtime_minutes: Mapping[str, int],
    zone_watering_coefficients: Mapping[str, float],
    zone_watering_profiles: Mapping[str, str],
    zone_sprinkler_wind_profiles: Mapping[str, str],
    controller_watering_day_restrictions: Mapping[str, str],
    zone_watering_day_restrictions: Mapping[str, str],
    zone_runtime_banks: Mapping[str, Mapping[str, object]],
    start_time_by_device: Mapping[str, dt_time],
    end_time_by_device: Mapping[str, dt_time],
    automatic_window_enabled_by_device: Mapping[str, bool],
    automatic_window_preference_by_device: Mapping[str, str],
    automatic_window_max_minutes_by_device: Mapping[str, int],
    et_today_override_inches: float | None = None,
    zone_weather_stop_holds: Mapping[str, Mapping[str, object]] | None = None,
    zone_bucket_states: Mapping[str, BhyveZoneBucketState | Mapping[str, object]] | None = None,
    zone_root_depths: Mapping[str, float] | None = None,
    zone_soil_whc: Mapping[str, float] | None = None,
    zone_mad_values: Mapping[str, float] | None = None,
    zone_kc_values: Mapping[str, float] | None = None,
    zone_trigger_buffers: Mapping[str, float] | None = None,
    hourly_et_inches: float | None = None,
    et_source: str | None = None,
) -> BhyveControllerPlan:
    """Build the plan snapshot for a single controller."""
    zone_bucket_states = zone_bucket_states or {}
    zone_root_depths = zone_root_depths or {}
    zone_soil_whc = zone_soil_whc or {}
    zone_mad_values = zone_mad_values or {}
    zone_kc_values = zone_kc_values or {}
    zone_trigger_buffers = zone_trigger_buffers or {}

    today = now_local.date()
    today_key = today.isoformat()
    weekday_key = _weekday_key(today)
    weekday_name = _weekday_label(weekday_key)
    controller_day_restriction = _controller_day_restriction(
        controller.device_id,
        weekday_key,
        controller_watering_day_restrictions,
    )
    allowed_days_per_week = _allowed_watering_days_per_week(
        controller,
        zone_watering_profiles,
        controller_watering_day_restrictions,
        zone_watering_day_restrictions,
    )
    effective_rain_24h = calc_effective_rain(daily_rain_inches, rain_active_hours_24h)
    average_rain_rate_inches_per_hour = None
    if rain_active_hours_24h is not None and rain_active_hours_24h > 0 and daily_rain_inches > 0:
        average_rain_rate_inches_per_hour = round(
            daily_rain_inches / max(rain_active_hours_24h, _EFFECTIVE_RAIN_MIN_ACTIVE_HOURS),
            3,
        )

    calculated_et_today, et_multiplier = calc_daily_et_inches(
        today,
        latitude,
        temperature_f,
        uv_index,
        irradiance_w_m2,
        humidity_percent,
        wind_speed_mph,
    )
    et_today = (
        round(float(et_today_override_inches), 3)
        if et_today_override_inches is not None
        else calculated_et_today
    )

    recent_records = sorted(daily_records, key=lambda record: record.date)[
        -WATER_BALANCE_WINDOW_DAYS:
    ]
    effective_rain_7d = round(
        sum(record.effective_rain_inches for record in recent_records),
        3,
    )
    raw_rain_7d = round(sum(record.raw_rain_inches for record in recent_records), 3)
    et_7d = round(sum(record.et_inches for record in recent_records), 3)
    daylight_hours = max(1, daylight_hours_per_day())
    derived_hourly_et = (
        round(max(0.0, calculated_et_today) / daylight_hours, 4)
        if calculated_et_today > 0
        else 0.0
    )
    hourly_et_inches = round(
        max(0.0, float(hourly_et_inches if hourly_et_inches is not None else derived_hourly_et)),
        4,
    )
    et_source = et_source or "computed_daily_fallback"
    weekly_target_inches = round(hourly_et_inches * daylight_hours * 7, 3)

    since_utc = (now_local - timedelta(days=WATER_BALANCE_WINDOW_DAYS)).astimezone()
    since_14d_utc = (now_local - timedelta(days=14)).astimezone()
    since_21d_utc = (now_local - timedelta(days=21)).astimezone()
    recent_runs = _collect_recent_controller_runs(controller, since_21d_utc)
    recent_run_count_14d = 0
    recent_run_count_21d = len(recent_runs)
    recent_runtime_minutes_14d = 0
    recent_runtime_minutes_21d = 0
    for run in recent_runs:
        if run.end_ts is None:
            continue
        run_dt = datetime.fromtimestamp(run.end_ts, tz=since_21d_utc.tzinfo)
        recent_runtime_minutes_21d += run.duration_minutes
        if run_dt >= since_14d_utc:
            recent_run_count_14d += 1
            recent_runtime_minutes_14d += run.duration_minutes

    last_watering = recent_runs[0] if recent_runs else None
    irrigation_7d_minutes = 0
    irrigation_7d_inches = 0.0
    zone_weather_stop_holds = zone_weather_stop_holds or {}
    minimum_run_threshold_minutes = max(0, int(round(minimum_run_threshold_minutes)))
    max_watering_wind_speed_mph = float(
        clamp(max_watering_wind_speed_mph, 0.0, MAX_WATERING_WIND_SPEED_MPH)
    )
    min_watering_temperature_f = float(
        clamp(
            min_watering_temperature_f,
            MIN_WATERING_TEMPERATURE_F,
            MAX_WATERING_TEMPERATURE_F,
        )
    )

    controller_wind_profile = "Per-zone"
    effective_wind_profile = "Per-zone"
    effective_max_watering_wind_speed_mph = max_watering_wind_speed_mph
    max_watering_gust_speed_mph = clamp(
        max(
            max_watering_wind_speed_mph + SPRAY_GUST_THRESHOLD_OFFSET_MPH,
            max_watering_wind_speed_mph * SPRAY_GUST_THRESHOLD_FACTOR,
        ),
        0.0,
        MAX_WATERING_GUST_SPEED_MPH,
    )
    cold_hold_reason, _cold_hold_active = _weather_hold_reason(
        temperature_f=temperature_f,
        min_watering_temperature_f=min_watering_temperature_f,
        wind_speed_mph=wind_speed_mph,
        max_watering_wind_speed_mph=None,
        wind_gust_mph=wind_gust_mph,
        max_watering_gust_speed_mph=None,
    )

    automatic_window_enabled = automatic_window_enabled_by_device.get(
        controller.device_id,
        True,
    )
    automatic_window_preference = normalize_automatic_window_preference(
        automatic_window_preference_by_device.get(
            controller.device_id,
            DEFAULT_AUTOMATIC_WINDOW_PREFERENCE,
        )
    )
    automatic_window_max_minutes = int(
        clamp(
            automatic_window_max_minutes_by_device.get(
                controller.device_id,
                DEFAULT_MAX_AUTOMATIC_WATERING_WINDOW_MINUTES,
            ),
            MIN_AUTOMATIC_WATERING_WINDOW_MINUTES,
            MAX_AUTOMATIC_WATERING_WINDOW_MINUTES,
        )
    )

    enabled_non_disabled_zone_count = 0
    configured_zone_count = 0
    zone_runtime_data: list[dict[str, object]] = []
    active_zone_balance_data: list[dict[str, object]] = []

    for zone in controller.zones:
        runtime_key = f"{controller.device_id}:{zone.zone_number}"
        watering_profile = normalize_zone_watering_profile(
            zone_watering_profiles.get(runtime_key, ZONE_WATERING_PROFILE_DEFAULT)
        )
        application_rate_inches_per_hour = round(
            max(0.0, float(zone_application_rates.get(runtime_key, 0.0) or 0.0)),
            2,
        )
        application_rate_configured = application_rate_inches_per_hour > 0
        user_watering_coefficient = float(
            clamp(
                zone_watering_coefficients.get(runtime_key, 1.0),
                MIN_ZONE_WATERING_COEFFICIENT,
                MAX_ZONE_WATERING_COEFFICIENT,
            )
        )
        agronomy = resolve_zone_agronomy(
            device_id=controller.device_id,
            zone_number=zone.zone_number,
            zone_watering_profiles=zone_watering_profiles,
            zone_root_depths=zone_root_depths,
            zone_soil_whc=zone_soil_whc,
            zone_mad_values=zone_mad_values,
            zone_kc_values=zone_kc_values,
            zone_trigger_buffers=zone_trigger_buffers,
        )
        root_depth_inches = float(agronomy["root_depth_in"])
        soil_whc_in_per_in = float(agronomy["soil_whc_in_per_in"])
        mad = float(agronomy["mad"])
        kc = float(agronomy["kc"])
        trigger_buffer_inches = float(agronomy["trigger_buffer_in"])
        capacity_inches = float(agronomy["capacity_in"])
        exposure_factor = _zone_exposure_factor(zone)
        sprinkler_head_type = resolve_zone_wind_profile(
            zone_sprinkler_wind_profiles.get(runtime_key)
        )
        recent_minutes, recent_inches = calc_recent_zone_irrigation(
            zone,
            since_utc,
            application_rate_inches_per_hour,
        )
        irrigation_7d_minutes += recent_minutes
        irrigation_7d_inches += recent_inches

        zone_hourly_et = zone_hourly_et_inches(
            hourly_et_inches=hourly_et_inches,
            kc=kc,
            exposure_factor=exposure_factor,
            overall_watering_coefficient=overall_watering_coefficient,
            zone_watering_coefficient=user_watering_coefficient,
        )
        zone_daily_et = zone_daily_et_inches(zone_hourly_et)
        zone_weekly_target = round(zone_daily_et * 7.0, 3)

        bucket_record = (
            zone_bucket_states.get(runtime_key)
            or zone_bucket_states.get(str(zone.zone_number))
        )
        bucket_state = coerce_zone_bucket_state(
            bucket_record,
            capacity_inches=capacity_inches,
        )
        if bucket_state is None:
            legacy_deficit_inches, _, _, _, _ = estimate_legacy_zone_deficit_inches(
                zone=zone,
                since_utc=since_utc,
                effective_rain_7d_inches=effective_rain_7d,
                et_7d_inches=et_7d,
                zone_application_rate_inches_per_hour=application_rate_inches_per_hour,
                weekly_target_inches=weekly_target_inches,
                overall_watering_coefficient=overall_watering_coefficient,
                zone_watering_coefficient=user_watering_coefficient,
                kc=kc,
            )
            bootstrapped_water = capacity_inches if legacy_deficit_inches <= 0 else clamp_bucket_current_water(
                capacity_inches - legacy_deficit_inches,
                capacity_inches,
            )
            latest_event_key = None
            if zone.latest_event is not None:
                latest_event_key = zone_irrigation_event_key(zone.latest_event)
            bucket_state = BhyveZoneBucketState(
                capacity_inches=round(capacity_inches, 3),
                current_water_inches=bootstrapped_water,
                last_bucket_update=now_local.isoformat(),
                last_et_hour_key=(
                    now_local.strftime("%Y-%m-%dT%H")
                    if ET_DAYLIGHT_START_HOUR <= now_local.hour < ET_DAYLIGHT_END_HOUR
                    else None
                ),
                last_authoritative_et_date=None,
                last_effective_rain_date=today_key,
                last_effective_rain_total_inches=effective_rain_24h,
                last_irrigation_event_key=latest_event_key,
            )

        current_water_inches = clamp_bucket_current_water(
            bucket_state.current_water_inches,
            capacity_inches,
        )
        if watering_profile == ZONE_WATERING_PROFILE_DISABLED:
            current_water_inches = clamp_bucket_current_water(
                capacity_inches,
                capacity_inches,
            )
            zone_hourly_et = 0.0
            zone_daily_et = 0.0
            zone_weekly_target = 0.0
        deficit_inches = derive_deficit_inches(current_water_inches, capacity_inches)
        raw_deficit_inches = deficit_inches
        scale_factor = round(
            clamp(
                deficit_inches / max(capacity_inches, 0.001),
                0.0,
                1.0,
            ),
            3,
        )
        days_since_last_watering = _zone_last_run_age_days(zone, now_local)
        days_until_due = None
        if zone_daily_et > 0 and current_water_inches > trigger_buffer_inches:
            days_until_due = round(
                max(0.0, (current_water_inches - trigger_buffer_inches) / zone_daily_et),
                2,
            )

        if watering_profile == ZONE_WATERING_PROFILE_VEGETABLE_GARDEN:
            target_interval_days = _vegetable_garden_interval_days(today, temperature_f)
            zone_session_cap = min(
                max_session_minutes(sprinkler_head_type),
                _vegetable_garden_session_cap(zone, today, temperature_f),
            )
        elif watering_profile == ZONE_WATERING_PROFILE_TREES_SHRUBS:
            target_interval_days = _trees_shrubs_interval_days(zone, today, temperature_f)
            zone_session_cap = max_session_minutes(sprinkler_head_type)
        elif watering_profile == ZONE_WATERING_PROFILE_DROUGHT_TOLERANT:
            target_interval_days = _water_efficient_interval_days(zone, today, temperature_f)
            zone_session_cap = max_session_minutes(sprinkler_head_type)
        else:
            target_interval_days = None
            zone_session_cap = max_session_minutes(sprinkler_head_type)

        potential_runtime_minutes = 0
        if application_rate_configured and deficit_inches > 0:
            potential_runtime_minutes = max(
                0,
                round((deficit_inches / application_rate_inches_per_hour) * 60.0),
            )

        if zone.enabled and watering_profile != ZONE_WATERING_PROFILE_DISABLED:
            enabled_non_disabled_zone_count += 1
        if (
            zone.enabled
            and watering_profile != ZONE_WATERING_PROFILE_DISABLED
            and application_rate_configured
        ):
            configured_zone_count += 1
            active_zone_balance_data.append(
                {
                    "zone_name": zone.name,
                    "deficit_inches": deficit_inches,
                    "raw_deficit_inches": raw_deficit_inches,
                    "zone_weekly_target": zone_weekly_target,
                }
            )

        zone_runtime_data.append(
            {
                "zone": zone,
                "runtime_key": runtime_key,
                "application_rate_configured": application_rate_configured,
                "application_rate_inches_per_hour": application_rate_inches_per_hour,
                "root_depth_inches": root_depth_inches,
                "soil_whc_in_per_in": soil_whc_in_per_in,
                "mad": mad,
                "kc": kc,
                "capacity_inches": capacity_inches,
                "current_water_inches": current_water_inches,
                "deficit_inches": deficit_inches,
                "raw_deficit_inches": raw_deficit_inches,
                "trigger_buffer_inches": trigger_buffer_inches,
                "projected_et_draw_inches": 0.0,
                "projected_daylight_hours": 0.0,
                "projected_remaining_inches": current_water_inches,
                "trigger_active": False,
                "potential_runtime_minutes": potential_runtime_minutes,
                "requested_runtime_minutes": 0,
                "scheduled_runtime_minutes": 0,
                "minimum_run_threshold_minutes": minimum_run_threshold_minutes,
                "effective_minimum_run_threshold_minutes": minimum_run_threshold_minutes,
                "runtime_bank_minutes": 0,
                "runtime_bank_increment_minutes": 0,
                "scale_factor": scale_factor,
                "weekly_target_inches": zone_weekly_target,
                "recent_runtime_minutes_7d": recent_minutes,
                "recent_irrigation_inches_7d": recent_inches,
                "remaining_weekly_runtime_minutes": None,
                "capped_by_session_limit": False,
                "capped_by_weekly_limit": False,
                "allowable_depletion_inches": capacity_inches,
                "crop_coefficient": kc,
                "user_watering_coefficient": round(user_watering_coefficient, 3),
                "zone_demand_multiplier": round(
                    max(0.0, zone_hourly_et / max(hourly_et_inches, 0.0001)),
                    3,
                ),
                "weekday_name": weekday_name,
                "controller_day_restriction": controller_day_restriction,
                "zone_day_restriction": DAY_RESTRICTION_AUTO,
                "schedule_hold_active": False,
                "allowed_days_per_week": max(1, allowed_days_per_week),
                "sprinkler_head_type": sprinkler_head_type,
                "effective_max_watering_wind_speed_mph": None,
                "max_watering_gust_speed_mph": None,
                "weather_hold_active": False,
                "exposure_factor": round(exposure_factor, 3),
                "seasonal_factor": 1.0,
                "soil_storage_factor": 1.0,
                "storage_buffer_days": (
                    round(current_water_inches / zone_daily_et, 2)
                    if zone_daily_et > 0
                    else None
                ),
                "session_limit_minutes": zone_session_cap,
                "watering_profile": watering_profile,
                "water_efficient_mode": watering_profile == ZONE_WATERING_PROFILE_DROUGHT_TOLERANT,
                "trees_shrubs_mode": watering_profile == ZONE_WATERING_PROFILE_TREES_SHRUBS,
                "vegetable_garden_mode": watering_profile == ZONE_WATERING_PROFILE_VEGETABLE_GARDEN,
                "banked_by_weather_hold": False,
                "target_interval_days": target_interval_days,
                "days_since_last_watering": days_since_last_watering,
                "days_until_due": days_until_due,
                "forced_by_skip_limit": False,
                "deferred_by_window_limit": False,
                "reason": "",
                "zone_hourly_et_inches": zone_hourly_et,
                "zone_daily_et_inches": zone_daily_et,
            }
        )

    total_potential_runtime_minutes = sum(
        int(zone_data["potential_runtime_minutes"])
        for zone_data in zone_runtime_data
        if zone_data["application_rate_configured"]
        and zone_data["zone"].enabled
        and str(zone_data["watering_profile"]) != ZONE_WATERING_PROFILE_DISABLED
    )
    suggested_start_time, suggested_end_time, automatic_window_reason = suggest_watering_window(
        zones=controller.zones,
        for_date=today,
        latitude=latitude,
        longitude=longitude,
        utc_offset_hours=(now_local.utcoffset() or timedelta()).total_seconds() / 3600.0,
        temperature_f=temperature_f,
        total_runtime_minutes=total_potential_runtime_minutes,
        allowed_watering_days_per_week=allowed_days_per_week,
        maximum_window_minutes=automatic_window_max_minutes,
        timing_preference=automatic_window_preference,
    )
    effective_start_time = (
        suggested_start_time
        if automatic_window_enabled
        else start_time_by_device.get(
            f"{controller.device_id}:start",
            DEFAULT_WATERING_START_TIME,
        )
    )
    effective_end_time = (
        suggested_end_time
        if automatic_window_enabled
        else end_time_by_device.get(
            f"{controller.device_id}:end",
            DEFAULT_WATERING_END_TIME,
        )
    )

    forecast_probability = normalize_probability(forecast_rain_probability)
    rain_delay_days = _rain_delay_days(recent_records, today)
    dry_days_streak = _dry_days_streak(recent_records, today)

    due_zone_count = 0
    runnable_zone_count = 0
    trigger_weather_hold_count = 0
    trigger_schedule_hold_count = 0
    any_zone_weather_hold = False
    any_zone_schedule_hold = False
    peak_deficit_zone_name = None
    deficit_inches = 0.0
    raw_deficit_inches = 0.0
    active_zone_weekly_target = weekly_target_inches
    if active_zone_balance_data:
        peak_zone_balance = max(
            active_zone_balance_data,
            key=lambda item: float(item["deficit_inches"]),
        )
        deficit_inches = round(float(peak_zone_balance["deficit_inches"]), 3)
        raw_deficit_inches = round(float(peak_zone_balance["raw_deficit_inches"]), 3)
        peak_deficit_zone_name = str(peak_zone_balance["zone_name"])
        active_zone_weekly_target = max(
            float(item["zone_weekly_target"]) for item in active_zone_balance_data
        )

    for zone_data in zone_runtime_data:
        zone = zone_data["zone"]
        runtime_key = str(zone_data["runtime_key"])
        application_rate_inches_per_hour = float(zone_data["application_rate_inches_per_hour"])
        application_rate_configured = bool(zone_data["application_rate_configured"])
        sprinkler_head_type = str(zone_data["sprinkler_head_type"])
        zone_session_cap = int(zone_data["session_limit_minutes"])
        zone_weekly_cap = max_weekly_runtime_minutes.get(runtime_key, 0)
        recent_minutes = int(zone_data["recent_runtime_minutes_7d"])
        remaining_weekly_runtime: int | None = None
        if zone_weekly_cap > 0:
            remaining_weekly_runtime = max(0, zone_weekly_cap - recent_minutes)
        zone_data["remaining_weekly_runtime_minutes"] = remaining_weekly_runtime
        zone_day_restriction = _zone_day_restriction(
            controller.device_id,
            zone.zone_number,
            weekday_key,
            zone_watering_day_restrictions,
        )
        zone_data["zone_day_restriction"] = zone_day_restriction
        weekday_allowed = _zone_allowed_on_weekday(
            controller.device_id,
            zone.zone_number,
            weekday_key,
            controller_watering_day_restrictions,
            zone_watering_day_restrictions,
        )
        zone_allowed_days_per_week = sum(
            1
            for allowed_day_key in WEEKDAY_KEYS
            if _zone_allowed_on_weekday(
                controller.device_id,
                zone.zone_number,
                allowed_day_key,
                controller_watering_day_restrictions,
                zone_watering_day_restrictions,
            )
        )
        zone_data["allowed_days_per_week"] = zone_allowed_days_per_week

        next_window_start = compute_next_window_start(
            now_local=now_local,
            controller=controller,
            allowed_start_time=effective_start_time,
            allowed_end_time=effective_end_time,
            zone_watering_profiles=zone_watering_profiles,
            controller_day_restrictions=controller_watering_day_restrictions,
            zone_day_restrictions=zone_watering_day_restrictions,
        )
        projected_et_draw_inches, projected_daylight_hours = project_et_draw(
            float(zone_data["zone_hourly_et_inches"]),
            now_local,
            next_window_start,
        )
        current_water_inches = float(zone_data["current_water_inches"])
        projected_remaining_inches = round(
            current_water_inches - projected_et_draw_inches,
            3,
        )
        trigger_buffer_inches = float(zone_data["trigger_buffer_inches"])
        deficit_inches_zone = float(zone_data["deficit_inches"])
        trigger_active = False
        if (
            zone.enabled
            and str(zone_data["watering_profile"]) != ZONE_WATERING_PROFILE_DISABLED
            and application_rate_configured
        ):
            if projected_daylight_hours == 0:
                trigger_active = deficit_inches_zone >= trigger_buffer_inches
            else:
                trigger_active = projected_remaining_inches <= trigger_buffer_inches
        zone_data["projected_et_draw_inches"] = projected_et_draw_inches
        zone_data["projected_daylight_hours"] = projected_daylight_hours
        zone_data["projected_remaining_inches"] = projected_remaining_inches
        zone_data["trigger_active"] = trigger_active

        zone_effective_max_watering_wind_speed_mph, zone_max_watering_gust_speed_mph = calc_wind_stop_thresholds(
            max_watering_wind_speed_mph,
            sprinkler_head_type,
        )
        zone_data["effective_max_watering_wind_speed_mph"] = zone_effective_max_watering_wind_speed_mph
        zone_data["max_watering_gust_speed_mph"] = zone_max_watering_gust_speed_mph
        zone_weather_stop_hold = zone_weather_stop_holds.get(str(zone.zone_number), {})
        zone_weather_stop_held_today = (
            bool(zone_weather_stop_hold)
            and str(zone_weather_stop_hold.get("date") or "") == today_key
        )
        persisted_zone_weather_hold_reason = None
        if zone_weather_stop_held_today:
            persisted_zone_weather_hold_reason = str(
                zone_weather_stop_hold.get("reason")
                or "Watering for this zone was already stopped earlier today because of weather."
            )
        zone_weather_hold_reason, zone_weather_hold_active = _weather_hold_reason(
            persisted_reason=persisted_zone_weather_hold_reason,
            temperature_f=temperature_f,
            min_watering_temperature_f=min_watering_temperature_f,
            wind_speed_mph=wind_speed_mph,
            max_watering_wind_speed_mph=zone_effective_max_watering_wind_speed_mph,
            wind_gust_mph=wind_gust_mph,
            max_watering_gust_speed_mph=zone_max_watering_gust_speed_mph,
        )
        zone_data["weather_hold_active"] = zone_weather_hold_active

        requested_runtime = 0
        zone_reason = ""
        forced_by_skip_limit = False
        capped_by_session_limit = False
        capped_by_weekly_limit = False
        schedule_hold_active = False
        effective_minimum_run_threshold_minutes = minimum_run_threshold_minutes

        if str(zone_data["watering_profile"]) == ZONE_WATERING_PROFILE_DISABLED:
            zone_reason = "Zone is excluded from planning (Disabled profile)."
        elif not zone.enabled:
            zone_reason = "Zone is disabled in B-hyve."
        elif not application_rate_configured:
            zone_reason = (
                "Application rate is not configured. Calibrate this zone or enter inches per hour to enable planning."
            )
        elif deficit_inches_zone <= 0:
            zone_reason = "The zone bucket is full right now."
        elif not trigger_active:
            zone_reason = (
                f"Projected remaining usable water stays above the {trigger_buffer_inches:.2f} in trigger buffer until the next allowed window."
            )
        else:
            requested_runtime = max(
                0,
                round((deficit_inches_zone / application_rate_inches_per_hour) * 60.0),
            )
            if zone_weekly_cap > 0 and remaining_weekly_runtime is not None:
                if remaining_weekly_runtime <= 0:
                    requested_runtime = 0
                    capped_by_weekly_limit = True
                    zone_reason = "Weekly runtime cap has been reached."
                elif requested_runtime > remaining_weekly_runtime:
                    requested_runtime = remaining_weekly_runtime
                    capped_by_weekly_limit = True
            if requested_runtime > zone_session_cap:
                requested_runtime = zone_session_cap
                capped_by_session_limit = True
            if (
                requested_runtime > 0
                and effective_minimum_run_threshold_minutes > 0
                and requested_runtime < effective_minimum_run_threshold_minutes
            ):
                days_since_last_watering = zone_data["days_since_last_watering"]
                if (
                    days_since_last_watering is not None
                    and float(days_since_last_watering) >= FORCE_MINIMUM_RUN_AFTER_DAYS
                ):
                    forced_by_skip_limit = True
                else:
                    requested_runtime = 0
                    zone_reason = (
                        f"Projected watering need is below the {effective_minimum_run_threshold_minutes}-minute minimum run threshold."
                    )
            if requested_runtime > 0 and zone_weather_hold_active:
                requested_runtime = 0
                zone_reason = zone_weather_hold_reason
            elif requested_runtime > 0 and not weekday_allowed:
                requested_runtime = 0
                schedule_hold_active = True
                zone_reason = _weekday_restriction_reason(
                    weekday_label=weekday_name,
                    controller_day_restriction=controller_day_restriction,
                    zone_day_restriction=zone_day_restriction,
                )
            elif requested_runtime > 0:
                zone_reason = (
                    "Zone is projected to reach the trigger buffer before the next allowed window."
                )

        if trigger_active:
            due_zone_count += 1
            if zone_weather_hold_active:
                trigger_weather_hold_count += 1
                any_zone_weather_hold = True
            if schedule_hold_active:
                trigger_schedule_hold_count += 1
                any_zone_schedule_hold = True
        if requested_runtime > 0:
            runnable_zone_count += 1

        _LOGGER.debug(
            "%s: current=%.2fin deficit=%.2fin projected_draw=%.2fin over %.1f daylight hrs projected_remaining=%.2fin trigger=%s",
            zone.name,
            current_water_inches,
            deficit_inches_zone,
            projected_et_draw_inches,
            projected_daylight_hours,
            projected_remaining_inches,
            trigger_active,
        )

        zone_data["requested_runtime_minutes"] = requested_runtime
        zone_data["scheduled_runtime_minutes"] = requested_runtime
        zone_data["capped_by_session_limit"] = capped_by_session_limit
        zone_data["capped_by_weekly_limit"] = capped_by_weekly_limit
        zone_data["schedule_hold_active"] = schedule_hold_active
        zone_data["forced_by_skip_limit"] = forced_by_skip_limit
        zone_data["reason"] = zone_reason

    if enabled_non_disabled_zone_count <= 0:
        decision = "skip"
        reason = "All zones are disabled or excluded from planning."
    elif configured_zone_count <= 0:
        decision = "not_configured"
        reason = (
            "No enabled zones have an application rate yet. Calibrate a zone or enter inches per hour to enable planning."
        )
    elif due_zone_count <= 0:
        if rain_delay_days > 0 and effective_rain_24h > 0:
            decision = "rain_delay"
            reason = "Recent effective rain is still carrying over."
        else:
            decision = "skip"
            reason = "No zones need water right now; the planner is monitoring bucket depletion."
    else:
        defer_for_forecast = (
            forecast_probability is not None
            and forecast_probability >= FORECAST_RAIN_DEFER_PROBABILITY
            and (forecast_rain_amount_inches or 0.0) >= FORECAST_RAIN_DEFER_THRESHOLD_INCHES
            and deficit_inches <= max(
                active_zone_weekly_target * FORECAST_RAIN_DEFER_DEFICIT_FACTOR,
                DEFAULT_ZONE_TRIGGER_BUFFER_INCHES,
            )
            and dry_days_streak < 3
        )
        if defer_for_forecast:
            decision = "defer"
            reason = "Rain is likely in the next 24 hours."
        elif runnable_zone_count > 0:
            decision = "run"
            reason = "One or more zones are projected to reach the trigger buffer before the next allowed window."
        elif trigger_weather_hold_count > 0:
            decision = "weather_hold"
            held_reasons = [
                str(zone_data["reason"])
                for zone_data in zone_runtime_data
                if bool(zone_data["trigger_active"]) and bool(zone_data["weather_hold_active"])
            ]
            reason = held_reasons[0] if held_reasons else cold_hold_reason
        elif trigger_schedule_hold_count > 0:
            decision = "restricted_day"
            reason = (
                f"{weekday_name} is disabled by the controller watering-day schedule."
            )
        else:
            decision = "skip"
            reason = "No zones currently need irrigation."

    if decision in {"rain_delay", "defer", "weather_hold", "restricted_day"}:
        for zone_data in zone_runtime_data:
            if bool(zone_data["trigger_active"]):
                zone_data["scheduled_runtime_minutes"] = 0
                if decision == "defer":
                    zone_data["reason"] = "Rain is likely in the next 24 hours."
                elif decision == "rain_delay":
                    zone_data["reason"] = "Recent effective rain is still carrying over."

    total_requested_runtime_minutes = sum(
        int(zone_data["requested_runtime_minutes"]) for zone_data in zone_runtime_data
    )
    suggested_start_time, suggested_end_time, automatic_window_reason = suggest_watering_window(
        zones=controller.zones,
        for_date=today,
        latitude=latitude,
        longitude=longitude,
        utc_offset_hours=(now_local.utcoffset() or timedelta()).total_seconds() / 3600.0,
        temperature_f=temperature_f,
        total_runtime_minutes=total_requested_runtime_minutes,
        allowed_watering_days_per_week=allowed_days_per_week,
        maximum_window_minutes=automatic_window_max_minutes,
        timing_preference=automatic_window_preference,
    )
    effective_start_time = (
        suggested_start_time
        if automatic_window_enabled
        else start_time_by_device.get(
            f"{controller.device_id}:start",
            DEFAULT_WATERING_START_TIME,
        )
    )
    effective_end_time = (
        suggested_end_time
        if automatic_window_enabled
        else end_time_by_device.get(
            f"{controller.device_id}:end",
            DEFAULT_WATERING_END_TIME,
        )
    )

    available_window_minutes = _window_duration_minutes(effective_start_time, effective_end_time)
    window_rotation_applied = False
    if decision == "run" and total_requested_runtime_minutes > available_window_minutes:
        window_rotation_applied = True
        remaining_window_minutes = available_window_minutes
        eligible_zone_data = sorted(
            (
                zone_data
                for zone_data in zone_runtime_data
                if int(zone_data["requested_runtime_minutes"]) > 0
            ),
            key=lambda zone_data: _zone_window_priority(
                zone_data["zone"],
                now_local=now_local,
                vegetable_garden_mode=bool(zone_data["vegetable_garden_mode"]),
                water_efficient_mode=bool(zone_data["water_efficient_mode"]),
                trees_shrubs_mode=bool(zone_data["trees_shrubs_mode"]),
                last_run_age_days=zone_data["days_since_last_watering"],
                recent_runtime_minutes_7d=int(zone_data["recent_runtime_minutes_7d"]),
            ),
        )
        for zone_data in eligible_zone_data:
            requested_runtime = int(zone_data["requested_runtime_minutes"])
            if requested_runtime <= 0:
                continue
            if requested_runtime <= remaining_window_minutes:
                remaining_window_minutes -= requested_runtime
                continue
            zone_data["scheduled_runtime_minutes"] = 0
            zone_data["deferred_by_window_limit"] = True
            zone_data["reason"] = (
                f"{zone_data['reason']} Deferred to a later watering cycle because the current watering window is already full."
            )
        automatic_window_reason = (
            f"{automatic_window_reason}; lower-priority due zones rotate into later cycles when demand exceeds the active watering window"
        )

    zone_plans: list[BhyveZonePlan] = []
    for zone_data in zone_runtime_data:
        zone = zone_data["zone"]
        recommended_runtime = int(zone_data["scheduled_runtime_minutes"])
        if zone_data["water_efficient_mode"] or zone_data["trees_shrubs_mode"]:
            cycle_minutes = (recommended_runtime,) if recommended_runtime > 0 else ()
        else:
            cycle_minutes = build_cycle_minutes(
                recommended_runtime,
                cycle_and_soak_threshold_minutes(str(zone_data["sprinkler_head_type"])),
            )
        zone_plans.append(
            BhyveZonePlan(
                device_id=controller.device_id,
                zone_number=zone.zone_number,
                zone_name=zone.name,
                enabled=zone.enabled,
                application_rate_configured=bool(zone_data["application_rate_configured"]),
                application_rate_inches_per_hour=float(zone_data["application_rate_inches_per_hour"]),
                root_depth_inches=float(zone_data["root_depth_inches"]),
                soil_whc_in_per_in=float(zone_data["soil_whc_in_per_in"]),
                mad=float(zone_data["mad"]),
                kc=float(zone_data["kc"]),
                capacity_inches=float(zone_data["capacity_inches"]),
                current_water_inches=float(zone_data["current_water_inches"]),
                deficit_inches=float(zone_data["deficit_inches"]),
                raw_deficit_inches=float(zone_data["raw_deficit_inches"]),
                trigger_buffer_inches=float(zone_data["trigger_buffer_inches"]),
                projected_et_draw_inches=float(zone_data["projected_et_draw_inches"]),
                projected_daylight_hours=float(zone_data["projected_daylight_hours"]),
                projected_remaining_inches=float(zone_data["projected_remaining_inches"]),
                zone_hourly_et_inches=float(zone_data["zone_hourly_et_inches"]),
                zone_daily_et_inches=float(zone_data["zone_daily_et_inches"]),
                trigger_active=bool(zone_data["trigger_active"]),
                requested_runtime_minutes=int(zone_data["requested_runtime_minutes"]),
                recommended_runtime_minutes=recommended_runtime,
                minimum_run_threshold_minutes=int(zone_data["minimum_run_threshold_minutes"]),
                effective_minimum_run_threshold_minutes=int(zone_data["effective_minimum_run_threshold_minutes"]),
                runtime_bank_minutes=0,
                runtime_bank_increment_minutes=0,
                cycle_minutes=cycle_minutes,
                scale_factor=float(zone_data["scale_factor"]),
                weekly_target_inches=float(zone_data["weekly_target_inches"]),
                estimated_application_inches=calc_zone_irrigation_inches(
                    float(zone_data["application_rate_inches_per_hour"]),
                    recommended_runtime,
                ),
                recent_runtime_minutes_7d=int(zone_data["recent_runtime_minutes_7d"]),
                recent_irrigation_inches_7d=float(zone_data["recent_irrigation_inches_7d"]),
                remaining_weekly_runtime_minutes=zone_data["remaining_weekly_runtime_minutes"],
                capped_by_session_limit=bool(zone_data["capped_by_session_limit"]),
                capped_by_weekly_limit=bool(zone_data["capped_by_weekly_limit"]),
                allowable_depletion_inches=float(zone_data["capacity_inches"]),
                crop_coefficient=float(zone_data["crop_coefficient"]),
                user_watering_coefficient=float(zone_data["user_watering_coefficient"]),
                zone_demand_multiplier=float(zone_data["zone_demand_multiplier"]),
                weekday_name=str(zone_data["weekday_name"]),
                controller_day_restriction=str(zone_data["controller_day_restriction"]),
                zone_day_restriction=str(zone_data["zone_day_restriction"]),
                schedule_hold_active=bool(zone_data["schedule_hold_active"]),
                allowed_days_per_week=int(zone_data["allowed_days_per_week"]),
                sprinkler_head_type=str(zone_data["sprinkler_head_type"]),
                effective_max_watering_wind_speed_mph=zone_data["effective_max_watering_wind_speed_mph"],
                max_watering_gust_speed_mph=zone_data["max_watering_gust_speed_mph"],
                weather_hold_active=bool(zone_data["weather_hold_active"]),
                exposure_factor=float(zone_data["exposure_factor"]),
                seasonal_factor=float(zone_data["seasonal_factor"]),
                soil_storage_factor=float(zone_data["soil_storage_factor"]),
                storage_buffer_days=zone_data["storage_buffer_days"],
                session_limit_minutes=int(zone_data["session_limit_minutes"]),
                watering_profile=str(zone_data["watering_profile"]),
                water_efficient_mode=bool(zone_data["water_efficient_mode"]),
                trees_shrubs_mode=bool(zone_data["trees_shrubs_mode"]),
                vegetable_garden_mode=bool(zone_data["vegetable_garden_mode"]),
                banked_by_weather_hold=False,
                target_interval_days=zone_data["target_interval_days"],
                days_since_last_watering=zone_data["days_since_last_watering"],
                days_until_due=zone_data["days_until_due"],
                forced_by_skip_limit=bool(zone_data["forced_by_skip_limit"]),
                deferred_by_window_limit=bool(zone_data["deferred_by_window_limit"]),
                reason=str(zone_data["reason"]),
            )
        )

    total_recommended_runtime_minutes = sum(
        zone_plan.recommended_runtime_minutes for zone_plan in zone_plans
    )
    allowed_now = is_within_watering_window(
        now_local.time(),
        effective_start_time,
        effective_end_time,
    )
    next_allowed_schedule_offset_days = _next_allowed_schedule_offset_days(
        controller=controller,
        start_date=today,
        start_offset_days=max(1, rain_delay_days if decision == "rain_delay" else 1),
        zone_watering_profiles=zone_watering_profiles,
        controller_day_restrictions=controller_watering_day_restrictions,
        zone_day_restrictions=zone_watering_day_restrictions,
    )
    next_cycle_start, next_cycle_end, next_cycle_status, next_cycle_reason = _project_next_cycle(
        now_local,
        decision,
        reason,
        effective_start_time,
        effective_end_time,
        rain_delay_days,
        next_allowed_schedule_offset_days=next_allowed_schedule_offset_days,
    )

    return BhyveControllerPlan(
        device_id=controller.device_id,
        nickname=controller.nickname,
        product_model=controller.product_model,
        decision=decision,
        reason=reason,
        deficit_inches=deficit_inches,
        raw_deficit_inches=raw_deficit_inches,
        deficit_basis="bucket_highest_zone_deficit",
        peak_deficit_zone_name=peak_deficit_zone_name,
        effective_rain_24h_inches=effective_rain_24h,
        rain_active_hours_24h=(
            None if rain_active_hours_24h is None else round(float(rain_active_hours_24h), 2)
        ),
        average_rain_rate_inches_per_hour=average_rain_rate_inches_per_hour,
        hourly_et_inches=hourly_et_inches,
        et_source=et_source,
        effective_rain_7d_inches=effective_rain_7d,
        raw_rain_7d_inches=raw_rain_7d,
        et_today_inches=et_today,
        et_7d_inches=et_7d,
        irrigation_7d_inches=round(irrigation_7d_inches, 3),
        irrigation_7d_minutes=irrigation_7d_minutes,
        weekly_target_inches=active_zone_weekly_target,
        et_multiplier=et_multiplier,
        location_latitude=round(latitude, 4),
        location_longitude=round(longitude, 4),
        location_source=location_source,
        temperature_f=temperature_f,
        humidity_percent=humidity_percent,
        wind_speed_mph=wind_speed_mph,
        wind_gust_mph=wind_gust_mph,
        min_watering_temperature_f=min_watering_temperature_f,
        max_watering_wind_speed_mph=max_watering_wind_speed_mph,
        effective_max_watering_wind_speed_mph=effective_max_watering_wind_speed_mph,
        max_watering_gust_speed_mph=max_watering_gust_speed_mph,
        sprinkler_wind_profile=controller_wind_profile,
        effective_wind_profile=effective_wind_profile,
        weather_hold_active=(decision == "weather_hold"),
        weather_stop_held_today=any_zone_weather_hold,
        rain_delay_days=rain_delay_days,
        dry_days_streak=dry_days_streak,
        next_cycle_start=next_cycle_start,
        next_cycle_end=next_cycle_end,
        next_cycle_status=next_cycle_status,
        next_cycle_reason=next_cycle_reason,
        last_watering_end=(
            datetime.fromtimestamp(last_watering.end_ts, tz=now_local.tzinfo).isoformat()
            if last_watering is not None and last_watering.end_ts is not None
            else None
        ),
        last_watering_zone_name=(last_watering.zone_name if last_watering is not None else None),
        last_watering_duration_minutes=(
            last_watering.duration_minutes if last_watering is not None else None
        ),
        recent_runtime_minutes_14d=recent_runtime_minutes_14d,
        recent_runtime_minutes_21d=recent_runtime_minutes_21d,
        recent_run_count_14d=recent_run_count_14d,
        recent_run_count_21d=recent_run_count_21d,
        recent_runs=recent_runs,
        automatic_window_enabled=automatic_window_enabled,
        automatic_window_preference=automatic_window_preference,
        automatic_window_max_minutes=automatic_window_max_minutes,
        suggested_start_time=suggested_start_time.strftime("%H:%M"),
        suggested_end_time=suggested_end_time.strftime("%H:%M"),
        effective_start_time=effective_start_time.strftime("%H:%M"),
        effective_end_time=effective_end_time.strftime("%H:%M"),
        available_window_minutes=available_window_minutes,
        automatic_window_reason=automatic_window_reason,
        current_weekday_name=weekday_name,
        controller_day_restriction=controller_day_restriction,
        allowed_days_per_week=allowed_days_per_week,
        total_requested_runtime_minutes=total_requested_runtime_minutes,
        total_recommended_runtime_minutes=total_recommended_runtime_minutes,
        window_rotation_applied=window_rotation_applied,
        allowed_now=allowed_now,
        weather_source_status="configured",
        forecast_rain_amount_inches=forecast_rain_amount_inches,
        forecast_rain_probability=forecast_probability,
        last_evaluated=now_local.isoformat(),
        zone_plans=tuple(zone_plans),
    )
