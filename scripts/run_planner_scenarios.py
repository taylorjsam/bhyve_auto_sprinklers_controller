"""Run representative irrigation-planner scenarios and validate expectations.

This harness loads the live planner module with lightweight stubs so scenario
coverage stays aligned with the actual integration math. It prints a scenario
matrix and exits non-zero if key calibration expectations regress.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, time as dt_time, timedelta, timezone
from enum import Enum
import importlib.util
from pathlib import Path
import sys
import types
from zoneinfo import ZoneInfo


SCENARIO_YEAR = datetime.now().year
TZ_DENVER = "America/Denver"
TZ_CHICAGO = "America/Chicago"
TZ_PHOENIX = "America/Phoenix"
TZ_LOS_ANGELES = "America/Los_Angeles"
TZ_NEW_YORK = "America/New_York"


@dataclass(frozen=True)
class RecentEventSpec:
    """Recent controller watering event used in validation scenarios."""

    zone_number: int
    days_ago: int
    duration_minutes: int
    schedule_name: str = "Recent plan"
    schedule_type: str = "SMART"


@dataclass(frozen=True)
class ScenarioSpec:
    """A planner-validation scenario."""

    name: str
    now_local: datetime
    latitude: float
    longitude: float
    temperature_f: float
    uv_index: float
    rain_pattern_inches: tuple[float, ...]
    humidity_percent: float | None = 45.0
    wind_speed_mph: float | None = 5.0
    wind_gust_mph: float | None = None
    forecast_amount_inches: float | None = None
    forecast_probability: float | None = None
    overall_watering_coefficient: float = 1.0
    minimum_run_threshold_minutes: int = 10
    max_watering_wind_speed_mph: float = 12.0
    min_watering_temperature_f: float = 40.0
    maximum_automatic_window_minutes: int = 480
    automatic_window_preference: str = "Morning (dawn)"
    max_weekly_runtime_minutes: dict[int, int] = field(default_factory=dict)
    zone_watering_coefficients: dict[int, float] = field(default_factory=dict)
    zone_application_rates: dict[int, float] = field(default_factory=dict)
    zone_profiles: dict[int, str] = field(default_factory=dict)
    zone_sprinkler_wind_profiles: dict[int, str] = field(default_factory=dict)
    controller_day_restrictions: dict[str, str] = field(default_factory=dict)
    zone_day_restrictions: dict[int, dict[str, str]] = field(default_factory=dict)
    zone_overrides: dict[int, dict[str, object]] = field(default_factory=dict)
    zone_runtime_banks: dict[int, int] = field(default_factory=dict)
    zone_weather_stop_holds: dict[int, dict[str, object]] | None = None
    recent_events: tuple[RecentEventSpec, ...] = ()
    use_automatic_window: bool = True
    manual_window_start: dt_time | None = None
    manual_window_end: dt_time | None = None
    note: str = ""


@dataclass(frozen=True)
class MatrixLocationSpec:
    """Location profile used to generate a larger scenario matrix."""

    name: str
    latitude: float
    longitude: float
    tz_name: str
    spring_temperature_f: float
    spring_uv_index: float
    summer_temperature_f: float
    summer_uv_index: float
    fall_temperature_f: float
    fall_uv_index: float


def _load_live_modules():
    """Load the current planner/models modules with lightweight dependency stubs."""

    root = (
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "bhyve_auto_sprinklers_controller"
    )

    ha = types.ModuleType("homeassistant")
    ha_const = types.ModuleType("homeassistant.const")

    class Platform(str, Enum):
        SENSOR = "sensor"
        VALVE = "valve"
        NUMBER = "number"
        BUTTON = "button"
        TIME = "time"
        SWITCH = "switch"
        SELECT = "select"

    ha_const.Platform = Platform

    ha_config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigEntry:
        def __class_getitem__(cls, _item):
            return cls

    ha_config_entries.ConfigEntry = ConfigEntry

    ha_helpers = types.ModuleType("homeassistant.helpers")
    ha_helpers_uc = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

    ha_helpers_uc.DataUpdateCoordinator = DataUpdateCoordinator

    pkg_cc = types.ModuleType("custom_components")
    pkg_ws = types.ModuleType("custom_components.bhyve_auto_sprinklers_controller")
    pkg_ws.__path__ = [str(root)]

    sys.modules.update(
        {
            "homeassistant": ha,
            "homeassistant.const": ha_const,
            "homeassistant.config_entries": ha_config_entries,
            "homeassistant.helpers": ha_helpers,
            "homeassistant.helpers.update_coordinator": ha_helpers_uc,
            "custom_components": pkg_cc,
            "custom_components.bhyve_auto_sprinklers_controller": pkg_ws,
        }
    )

    for name in ("const", "models", "planner"):
        spec = importlib.util.spec_from_file_location(
            f"custom_components.bhyve_auto_sprinklers_controller.{name}",
            root / f"{name}.py",
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

    return (
        sys.modules["custom_components.bhyve_auto_sprinklers_controller.models"],
        sys.modules["custom_components.bhyve_auto_sprinklers_controller.planner"],
    )


def _make_recent_events(models, now_local: datetime, specs: tuple[RecentEventSpec, ...]):
    """Convert recent-event specs into Bhyve event tuples keyed by zone number."""

    zone_events: dict[int, list[object]] = {}
    for spec in specs:
        end_dt = now_local - timedelta(days=spec.days_ago)
        end_dt = end_dt.replace(hour=6, minute=0, second=0, microsecond=0)
        zone_events.setdefault(spec.zone_number, []).append(
            models.BhyveLatestEvent(
                duration=spec.duration_minutes * 60,
                end_local=end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                end_ts=int(end_dt.timestamp()),
                schedule_name=spec.schedule_name,
                schedule_type=spec.schedule_type,
            )
        )
    return {
        zone_number: tuple(
            sorted(events, key=lambda item: item.end_ts or 0, reverse=True)
        )
        for zone_number, events in zone_events.items()
    }


def _build_controller(models, scenario: ScenarioSpec):
    """Build representative lawn/perennial/garden zones for a scenario."""

    recent_events = _make_recent_events(models, scenario.now_local, scenario.recent_events)

    grass = models.BhyveSprinklerZone(
        "ctrl1",
        "z1",
        1,
        "Backyard Right",
        True,
        900.0,
        "COOL_SEASON_GRASS",
        0.8,
        1.05,
        6.0,
        0.0,
        0.15,
        45.0,
        "LOTS_OF_SUN",
        "CLAY",
        "FLAT",
        "ROTARY_NOZZLE",
        0.7,
        65.0,
        None,
        True,
        2637,
        1200,
        "sched1",
        0.0,
        None,
        (),
        (),
        recent_events.get(1, (None,))[0] if recent_events.get(1) else None,
        recent_events.get(1, ()),
        (),
    )
    perennials = models.BhyveSprinklerZone(
        "ctrl1",
        "z2",
        2,
        "Front Yard Perennials",
        True,
        800.0,
        "PERENNIALS",
        0.55,
        0.0,
        10.0,
        0.0,
        0.15,
        80.0,
        "LOTS_OF_SUN",
        "CLAY",
        "FLAT",
        "DRIP_LINE",
        0.5,
        80.0,
        None,
        True,
        9818,
        900,
        "sched2",
        0.0,
        None,
        (),
        (),
        recent_events.get(2, (None,))[0] if recent_events.get(2) else None,
        recent_events.get(2, ()),
        (),
    )
    garden = models.BhyveSprinklerZone(
        "ctrl1",
        "z3",
        3,
        "Garden",
        True,
        300.0,
        "GARDEN",
        0.6896,
        0.0,
        9.7,
        13.8,
        0.2,
        30.0,
        "LOTS_OF_SUN",
        "CLAY_LOAM",
        "FLAT",
        "FIXED_SPRAY_HEAD",
        1.0,
        80.0,
        None,
        True,
        3387,
        600,
        "sched3",
        0.0,
        None,
        (),
        (),
        recent_events.get(3, (None,))[0] if recent_events.get(3) else None,
        recent_events.get(3, ()),
        (),
    )
    zones = [grass, perennials, garden]
    if scenario.zone_overrides:
        updated_zones = []
        for zone in zones:
            overrides = scenario.zone_overrides.get(zone.zone_number)
            if overrides:
                updated_zones.append(replace(zone, **overrides))
            else:
                updated_zones.append(zone)
        zones = updated_zones

    return models.BhyveSprinklerControllerSnapshot(
        "ctrl1",
        "Sprinklers",
        "BS_WK1",
        "Common",
        "Common",
        True,
        tuple(zones),
        None,
    )


def _build_records(models, planner, scenario: ScenarioSpec):
    """Build a seven-day water-balance ledger for the scenario."""

    if len(scenario.rain_pattern_inches) != 7:
        raise ValueError(f"Scenario {scenario.name} must provide exactly 7 rain values")

    et_inches, _ = planner.calc_daily_et_inches(
        scenario.now_local.date(),
        scenario.latitude,
        scenario.temperature_f,
        scenario.uv_index,
        scenario.humidity_percent,
        scenario.wind_speed_mph,
    )
    records = []
    for index, raw_rain in enumerate(scenario.rain_pattern_inches):
        record_date = scenario.now_local.date() - timedelta(days=6 - index)
        records.append(
            models.BhyveDailyWaterBalance(
                record_date.isoformat(),
                raw_rain,
                planner.calc_effective_rain(raw_rain),
                et_inches,
            )
        )
    return tuple(records)


def _default_zone_application_rates() -> dict[int, float]:
    """Return representative effective application rates for the test yard."""

    return {
        1: 0.60,  # rotary lawn
        2: 0.40,  # drip perennials
        3: 1.00,  # standard-spray vegetable garden
    }


def _default_zone_sprinkler_head_types() -> dict[int, str]:
    """Return representative configured sprinkler head types for the test yard."""

    return {
        1: "Rotary / stream",
        2: "Drip / bubbler",
        3: "Standard spray",
    }


def _run_scenario(models, planner, scenario: ScenarioSpec):
    """Return the controller plan for a scenario."""

    controller = _build_controller(models, scenario)
    records = _build_records(models, planner, scenario)
    max_runtime = {
        f"ctrl1:{zone_number}": minutes
        for zone_number, minutes in scenario.max_weekly_runtime_minutes.items()
    }
    zone_watering_profiles = {
        f"ctrl1:{zone_number}": profile
        for zone_number, profile in scenario.zone_profiles.items()
    }
    zone_application_rates = _default_zone_application_rates()
    zone_application_rates.update(scenario.zone_application_rates)
    zone_application_rate_map = {
        f"ctrl1:{zone_number}": rate
        for zone_number, rate in zone_application_rates.items()
    }
    zone_sprinkler_wind_profiles = _default_zone_sprinkler_head_types()
    zone_sprinkler_wind_profiles.update(scenario.zone_sprinkler_wind_profiles)
    zone_sprinkler_wind_profiles = {
        f"ctrl1:{zone_number}": profile
        for zone_number, profile in zone_sprinkler_wind_profiles.items()
    }
    controller_day_restrictions = {
        f"ctrl1:{weekday_key}": mode
        for weekday_key, mode in scenario.controller_day_restrictions.items()
    }
    zone_day_restrictions = {
        f"ctrl1:{zone_number}:{weekday_key}": mode
        for zone_number, weekday_modes in scenario.zone_day_restrictions.items()
        for weekday_key, mode in weekday_modes.items()
    }
    zone_runtime_banks = {
        str(zone_number): {
            "pending_minutes": minutes,
            "last_accumulated_date": None,
            "last_accumulated_request_minutes": 0,
        }
        for zone_number, minutes in scenario.zone_runtime_banks.items()
    }
    zone_weather_stop_holds = {
        str(zone_number): hold
        for zone_number, hold in (scenario.zone_weather_stop_holds or {}).items()
    }
    start_time_by_device = {}
    end_time_by_device = {}
    automatic_window_enabled_by_device = {}
    automatic_window_max_minutes_by_device = {
        "ctrl1": scenario.maximum_automatic_window_minutes
    }
    if not scenario.use_automatic_window:
        automatic_window_enabled_by_device["ctrl1"] = False
    if scenario.manual_window_start is not None:
        start_time_by_device["ctrl1:start"] = scenario.manual_window_start
    if scenario.manual_window_end is not None:
        end_time_by_device["ctrl1:end"] = scenario.manual_window_end
    zone_watering_coefficients = {
        f"ctrl1:{zone_number}": coefficient
        for zone_number, coefficient in scenario.zone_watering_coefficients.items()
    }
    return planner.build_controller_plan(
        controller=controller,
        now_local=scenario.now_local,
        daily_records=records,
        daily_rain_inches=scenario.rain_pattern_inches[-1],
        rain_active_hours_24h=None,
        latitude=scenario.latitude,
        longitude=scenario.longitude,
        location_source="scenario_runner",
        temperature_f=scenario.temperature_f,
        uv_index=scenario.uv_index,
        irradiance_w_m2=None,
        humidity_percent=scenario.humidity_percent,
        wind_speed_mph=scenario.wind_speed_mph,
        wind_gust_mph=scenario.wind_gust_mph,
        forecast_rain_amount_inches=scenario.forecast_amount_inches,
        forecast_rain_probability=scenario.forecast_probability,
        overall_watering_coefficient=scenario.overall_watering_coefficient,
        minimum_run_threshold_minutes=scenario.minimum_run_threshold_minutes,
        max_watering_wind_speed_mph=scenario.max_watering_wind_speed_mph,
        min_watering_temperature_f=scenario.min_watering_temperature_f,
        zone_application_rates=zone_application_rate_map,
        max_weekly_runtime_minutes=max_runtime,
        zone_watering_coefficients=zone_watering_coefficients,
        zone_watering_profiles=zone_watering_profiles,
        zone_sprinkler_wind_profiles=zone_sprinkler_wind_profiles,
        controller_watering_day_restrictions=controller_day_restrictions,
        zone_watering_day_restrictions=zone_day_restrictions,
        zone_runtime_banks=zone_runtime_banks,
        start_time_by_device=start_time_by_device,
        end_time_by_device=end_time_by_device,
        automatic_window_enabled_by_device=automatic_window_enabled_by_device,
        automatic_window_preference_by_device={
            "ctrl1": scenario.automatic_window_preference
        },
        automatic_window_max_minutes_by_device=automatic_window_max_minutes_by_device,
        zone_weather_stop_holds=zone_weather_stop_holds,
    )


def _zone_runtime(plan, zone_name: str) -> int:
    """Return the recommended runtime for the named zone."""

    for zone in plan.zone_plans:
        if zone.zone_name == zone_name:
            return zone.recommended_runtime_minutes
    raise KeyError(zone_name)


def _zone_plan(plan, zone_name: str):
    """Return the full zone plan for the named zone."""

    for zone in plan.zone_plans:
        if zone.zone_name == zone_name:
            return zone
    raise KeyError(zone_name)


def _dt_for_zone(
    month: int,
    day: int,
    hour: int,
    minute: int,
    tz_name: str,
    *,
    year: int = SCENARIO_YEAR,
) -> datetime:
    """Return a timezone-aware datetime using an IANA time zone."""

    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=ZoneInfo(tz_name),
    )


def _utc_offset_hours(for_datetime: datetime) -> float:
    """Return the local UTC offset for the provided aware datetime."""

    return (for_datetime.utcoffset() or timedelta()).total_seconds() / 3600.0


def _matrix_locations() -> tuple[MatrixLocationSpec, ...]:
    """Return representative U.S. climate locations for matrix generation."""

    return (
        MatrixLocationSpec("Salt Lake City", 40.76, -111.89, TZ_DENVER, 65.0, 6.0, 95.0, 10.0, 72.0, 6.0),
        MatrixLocationSpec("St. George", 37.10, -113.58, TZ_DENVER, 74.0, 8.0, 105.0, 11.0, 83.0, 7.0),
        MatrixLocationSpec("Phoenix", 33.45, -112.07, TZ_PHOENIX, 78.0, 8.0, 106.0, 11.0, 92.0, 8.0),
        MatrixLocationSpec("Sacramento", 38.58, -121.49, TZ_LOS_ANGELES, 73.0, 7.0, 99.0, 10.0, 82.0, 7.0),
        MatrixLocationSpec("San Diego", 32.72, -117.16, TZ_LOS_ANGELES, 66.0, 6.0, 80.0, 8.0, 74.0, 6.0),
        MatrixLocationSpec("Seattle", 47.61, -122.33, TZ_LOS_ANGELES, 60.0, 5.0, 78.0, 7.0, 68.0, 4.0),
        MatrixLocationSpec("Denver", 39.74, -104.99, TZ_DENVER, 68.0, 7.0, 93.0, 10.0, 75.0, 6.0),
        MatrixLocationSpec("Minneapolis", 44.98, -93.26, TZ_CHICAGO, 60.0, 5.0, 86.0, 9.0, 70.0, 5.0),
        MatrixLocationSpec("Chicago", 41.88, -87.63, TZ_CHICAGO, 62.0, 5.0, 88.0, 9.0, 71.0, 5.0),
        MatrixLocationSpec("Boston", 42.36, -71.06, TZ_NEW_YORK, 63.0, 5.0, 84.0, 8.0, 70.0, 5.0),
        MatrixLocationSpec("Atlanta", 33.75, -84.39, TZ_NEW_YORK, 72.0, 7.0, 92.0, 9.0, 80.0, 6.0),
        MatrixLocationSpec("Houston", 29.76, -95.37, TZ_CHICAGO, 78.0, 8.0, 94.0, 10.0, 84.0, 7.0),
        MatrixLocationSpec("Miami", 25.76, -80.19, TZ_NEW_YORK, 82.0, 8.0, 91.0, 10.0, 86.0, 7.0),
    )


def _seasonal_air_inputs(location_name: str, season: str) -> tuple[float, float]:
    """Return representative humidity and wind inputs for a location-season pair."""

    profiles: dict[str, dict[str, tuple[float, float]]] = {
        "Salt Lake City": {"spring": (38.0, 8.0), "summer": (28.0, 10.0), "fall": (40.0, 8.0)},
        "St. George": {"spring": (30.0, 9.0), "summer": (18.0, 11.0), "fall": (28.0, 8.0)},
        "Phoenix": {"spring": (28.0, 8.0), "summer": (25.0, 10.0), "fall": (30.0, 8.0)},
        "Sacramento": {"spring": (50.0, 6.0), "summer": (38.0, 7.0), "fall": (48.0, 6.0)},
        "San Diego": {"spring": (68.0, 6.0), "summer": (62.0, 7.0), "fall": (58.0, 6.0)},
        "Seattle": {"spring": (72.0, 7.0), "summer": (60.0, 6.0), "fall": (78.0, 8.0)},
        "Denver": {"spring": (38.0, 9.0), "summer": (30.0, 10.0), "fall": (42.0, 8.0)},
        "Minneapolis": {"spring": (48.0, 8.0), "summer": (58.0, 8.0), "fall": (50.0, 8.0)},
        "Chicago": {"spring": (52.0, 10.0), "summer": (60.0, 9.0), "fall": (55.0, 9.0)},
        "Boston": {"spring": (58.0, 10.0), "summer": (68.0, 9.0), "fall": (60.0, 9.0)},
        "Atlanta": {"spring": (62.0, 7.0), "summer": (68.0, 7.0), "fall": (64.0, 6.0)},
        "Houston": {"spring": (72.0, 8.0), "summer": (78.0, 8.0), "fall": (74.0, 7.0)},
        "Miami": {"spring": (74.0, 9.0), "summer": (80.0, 10.0), "fall": (78.0, 8.0)},
    }
    seasonal_profile = profiles[location_name]
    return seasonal_profile[season]


def _generated_matrix_scenarios() -> tuple[ScenarioSpec, ...]:
    """Return a larger generated U.S. climate coverage matrix."""

    scenarios: list[ScenarioSpec] = []
    for location in _matrix_locations():
        spring_dt = _dt_for_zone(4, 18, 6, 0, location.tz_name)
        summer_dt = _dt_for_zone(7, 18, 5, 30, location.tz_name)
        fall_dt = _dt_for_zone(9, 18, 6, 0, location.tz_name)
        spring_humidity, spring_wind = _seasonal_air_inputs(location.name, "spring")
        summer_humidity, summer_wind = _seasonal_air_inputs(location.name, "summer")
        fall_humidity, fall_wind = _seasonal_air_inputs(location.name, "fall")

        scenarios.extend(
            (
                ScenarioSpec(
                    name=f"Matrix {location.name} spring dry",
                    now_local=spring_dt,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    temperature_f=location.spring_temperature_f,
                    uv_index=location.spring_uv_index,
                    humidity_percent=spring_humidity,
                    wind_speed_mph=spring_wind,
                    rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                    note="Generated matrix: spring dry-start baseline.",
                ),
                ScenarioSpec(
                    name=f"Matrix {location.name} spring wet",
                    now_local=spring_dt,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    temperature_f=max(location.spring_temperature_f - 2.0, 45.0),
                    uv_index=max(location.spring_uv_index - 1.0, 2.0),
                    humidity_percent=min(90.0, spring_humidity + 8.0),
                    wind_speed_mph=max(2.0, spring_wind - 1.0),
                    rain_pattern_inches=(0.35, 0.28, 0.42, 0.25, 0.30, 0.38, 0.22),
                    forecast_amount_inches=0.20,
                    forecast_probability=65.0,
                    note="Generated matrix: wet spring sequence should suppress irrigation demand heavily.",
                ),
                ScenarioSpec(
                    name=f"Matrix {location.name} summer dry",
                    now_local=summer_dt,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    temperature_f=location.summer_temperature_f,
                    uv_index=location.summer_uv_index,
                    humidity_percent=summer_humidity,
                    wind_speed_mph=summer_wind,
                    rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                    note="Generated matrix: peak-season dry baseline.",
                ),
                ScenarioSpec(
                    name=f"Matrix {location.name} spring cold snap hold",
                    now_local=spring_dt,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    temperature_f=36.0,
                    uv_index=max(location.spring_uv_index - 2.0, 2.0),
                    humidity_percent=min(90.0, spring_humidity + 6.0),
                    wind_speed_mph=max(2.0, spring_wind),
                    rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                    note="Generated matrix: cold-snap mornings should weather-hold and bank runtime.",
                ),
                ScenarioSpec(
                    name=f"Matrix {location.name} summer breezy below hold",
                    now_local=summer_dt,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    temperature_f=location.summer_temperature_f,
                    uv_index=location.summer_uv_index,
                    humidity_percent=summer_humidity,
                    wind_speed_mph=min(11.0, max(8.0, summer_wind + 2.0)),
                    rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                    note="Generated matrix: breezy mornings below the configured hold threshold should still run normally.",
                ),
                ScenarioSpec(
                    name=f"Matrix {location.name} summer windy hold",
                    now_local=summer_dt,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    temperature_f=max(location.summer_temperature_f - 2.0, 70.0),
                    uv_index=max(location.summer_uv_index - 0.5, 4.0),
                    humidity_percent=summer_humidity,
                    wind_speed_mph=max(15.0, summer_wind + 6.0),
                    rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                    note="Generated matrix: high-wind mornings should weather-hold and bank runtime.",
                ),
                ScenarioSpec(
                    name=f"Matrix {location.name} summer trace drizzle",
                    now_local=summer_dt,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    temperature_f=max(location.summer_temperature_f - 2.0, 70.0),
                    uv_index=max(location.summer_uv_index - 0.5, 4.0),
                    humidity_percent=min(90.0, summer_humidity + 6.0),
                    wind_speed_mph=max(2.0, summer_wind - 1.0),
                    rain_pattern_inches=(0.08, 0.08, 0.08, 0.08, 0.08, 0.08, 0.08),
                    note="Generated matrix: trace drizzle should buy very little effective-rain credit.",
                ),
                ScenarioSpec(
                    name=f"Matrix {location.name} summer light daily rain",
                    now_local=summer_dt,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    temperature_f=max(location.summer_temperature_f - 4.0, 70.0),
                    uv_index=max(location.summer_uv_index - 1.0, 4.0),
                    humidity_percent=min(90.0, summer_humidity + 10.0),
                    wind_speed_mph=max(2.0, summer_wind - 2.0),
                    rain_pattern_inches=(0.14, 0.14, 0.14, 0.14, 0.14, 0.14, 0.14),
                    note="Generated matrix: frequent light rain should reduce demand more than one storm.",
                ),
                ScenarioSpec(
                    name=f"Matrix {location.name} summer single soaker",
                    now_local=summer_dt,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    temperature_f=max(location.summer_temperature_f - 4.0, 70.0),
                    uv_index=max(location.summer_uv_index - 1.0, 4.0),
                    humidity_percent=min(90.0, summer_humidity + 4.0),
                    wind_speed_mph=summer_wind,
                    rain_pattern_inches=(0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0),
                    note="Generated matrix: one downpour should reduce demand, but not as much as meaningful frequent rain.",
                ),
                ScenarioSpec(
                    name=f"Matrix {location.name} summer forecast storm",
                    now_local=summer_dt,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    temperature_f=max(location.summer_temperature_f - 1.0, 72.0),
                    uv_index=max(location.summer_uv_index - 0.5, 4.0),
                    humidity_percent=min(90.0, summer_humidity + 8.0),
                    wind_speed_mph=summer_wind,
                    rain_pattern_inches=(0.0, 0.16, 0.0, 0.14, 0.0, 0.0, 0.10),
                    forecast_amount_inches=0.60,
                    forecast_probability=85.0,
                    note="Generated matrix: forecast hold should never increase watering vs. the dry baseline.",
                ),
                ScenarioSpec(
                    name=f"Matrix {location.name} fall dry",
                    now_local=fall_dt,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    temperature_f=location.fall_temperature_f,
                    uv_index=location.fall_uv_index,
                    humidity_percent=fall_humidity,
                    wind_speed_mph=fall_wind,
                    rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                    note="Generated matrix: fall dry-down should stay below peak summer demand.",
                ),
                ScenarioSpec(
                    name=f"Matrix {location.name} summer drought tolerant recent",
                    now_local=summer_dt,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    temperature_f=location.summer_temperature_f,
                    uv_index=location.summer_uv_index,
                    humidity_percent=summer_humidity,
                    wind_speed_mph=summer_wind,
                    rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                    zone_profiles={2: "Drought tolerant"},
                    recent_events=(
                        RecentEventSpec(zone_number=2, days_ago=2, duration_minutes=60),
                    ),
                    note="Generated matrix: drought-tolerant drip beds should keep spacing out after a recent deep run.",
                ),
                ScenarioSpec(
                    name=f"Matrix {location.name} summer trees compare",
                    now_local=summer_dt,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    temperature_f=location.summer_temperature_f,
                    uv_index=location.summer_uv_index,
                    humidity_percent=summer_humidity,
                    wind_speed_mph=summer_wind,
                    rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                    zone_profiles={2: "Trees / shrubs"},
                    note="Generated matrix: trees/shrubs profile should land between lawn default and drought-tolerant behavior.",
                ),
                ScenarioSpec(
                    name=f"Matrix {location.name} summer trees recent",
                    now_local=summer_dt,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    temperature_f=location.summer_temperature_f,
                    uv_index=location.summer_uv_index,
                    humidity_percent=summer_humidity,
                    wind_speed_mph=summer_wind,
                    rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                    zone_profiles={2: "Trees / shrubs"},
                    recent_events=(
                        RecentEventSpec(zone_number=2, days_ago=2, duration_minutes=60),
                    ),
                    note="Generated matrix: trees/shrubs profile should keep spacing out after a recent deeper run.",
                ),
                ScenarioSpec(
                    name=f"Matrix {location.name} summer vegetable due",
                    now_local=summer_dt,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    temperature_f=location.summer_temperature_f,
                    uv_index=location.summer_uv_index,
                    humidity_percent=summer_humidity,
                    wind_speed_mph=summer_wind,
                    rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                    zone_profiles={3: "Vegetable garden"},
                    recent_events=(
                        RecentEventSpec(zone_number=3, days_ago=2, duration_minutes=15),
                    ),
                    note="Generated matrix: raised-bed vegetable profile should run frequently but cap each session.",
                ),
            )
        )

    return tuple(scenarios)


def _targeted_edge_scenarios() -> tuple[ScenarioSpec, ...]:
    """Return additional targeted scenarios for remaining planner blind spots."""

    dallas_spring = _dt_for_zone(4, 22, 6, 0, TZ_CHICAGO)
    dallas_summer = _dt_for_zone(7, 22, 5, 30, TZ_CHICAGO)
    dallas_fall = _dt_for_zone(10, 10, 6, 0, TZ_CHICAGO)
    dallas_winter = _dt_for_zone(1, 18, 8, 0, TZ_CHICAGO)
    chicago_summer = _dt_for_zone(7, 22, 5, 30, TZ_CHICAGO)
    seattle_summer = _dt_for_zone(7, 22, 5, 30, TZ_LOS_ANGELES)

    return (
        ScenarioSpec(
            name="Dallas April warm-season turf dry",
            now_local=dallas_spring,
            latitude=32.78,
            longitude=-96.80,
            temperature_f=72.0,
            uv_index=7.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            zone_overrides={1: {"crop_type": "WARM_SEASON_GRASS"}},
            note="Warm-season turf should ramp more slowly in spring than cool-season turf.",
        ),
        ScenarioSpec(
            name="Dallas January warm-season dormant",
            now_local=dallas_winter,
            latitude=32.78,
            longitude=-96.80,
            temperature_f=48.0,
            uv_index=2.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            zone_overrides={1: {"crop_type": "WARM_SEASON_GRASS"}},
            note="Warm-season turf should effectively stay dormant in midwinter.",
        ),
        ScenarioSpec(
            name="Dallas April warm-season turf wet",
            now_local=dallas_spring,
            latitude=32.78,
            longitude=-96.80,
            temperature_f=68.0,
            uv_index=6.0,
            rain_pattern_inches=(0.20, 0.25, 0.0, 0.18, 0.0, 0.22, 0.10),
            zone_overrides={1: {"crop_type": "WARM_SEASON_GRASS"}},
            note="Warm-season turf spring wet pattern should stay heavily restrained.",
        ),
        ScenarioSpec(
            name="Dallas July warm-season turf dry",
            now_local=dallas_summer,
            latitude=32.78,
            longitude=-96.80,
            temperature_f=99.0,
            uv_index=10.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            zone_overrides={1: {"crop_type": "WARM_SEASON_GRASS"}},
            note="Warm-season turf should still water assertively in peak summer.",
        ),
        ScenarioSpec(
            name="Dallas October warm-season turf taper",
            now_local=dallas_fall,
            latitude=32.78,
            longitude=-96.80,
            temperature_f=68.0,
            uv_index=6.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            zone_overrides={1: {"crop_type": "WARM_SEASON_GRASS"}},
            note="Warm-season turf should taper more quickly in fall than peak summer.",
        ),
        ScenarioSpec(
            name="Dallas July warm-season shaded fixed spray",
            now_local=dallas_summer,
            latitude=32.78,
            longitude=-96.80,
            temperature_f=96.0,
            uv_index=9.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            zone_overrides={
                1: {
                    "crop_type": "WARM_SEASON_GRASS",
                    "exposure_type": "SOME_SHADE",
                    "nozzle_type": "FIXED_SPRAY_HEAD",
                    "smart_duration": 1200,
                }
            },
            note="Shade plus fixed-spray warm-season turf should stay below the full-sun rotary case.",
        ),
        ScenarioSpec(
            name="Chicago July forecast below defer amount",
            now_local=chicago_summer,
            latitude=41.88,
            longitude=-87.63,
            temperature_f=88.0,
            uv_index=9.0,
            rain_pattern_inches=(0.0, 0.16, 0.0, 0.14, 0.0, 0.0, 0.10),
            forecast_amount_inches=0.29,
            forecast_probability=80.0,
            note="Forecast just below the rain-amount threshold should not defer.",
        ),
        ScenarioSpec(
            name="Chicago July forecast at defer threshold",
            now_local=chicago_summer,
            latitude=41.88,
            longitude=-87.63,
            temperature_f=88.0,
            uv_index=9.0,
            rain_pattern_inches=(0.0, 0.16, 0.0, 0.14, 0.0, 0.0, 0.10),
            forecast_amount_inches=0.30,
            forecast_probability=70.0,
            note="Forecast at both amount and probability thresholds should defer when the deficit is moderate.",
        ),
        ScenarioSpec(
            name="Chicago July forecast below defer probability",
            now_local=chicago_summer,
            latitude=41.88,
            longitude=-87.63,
            temperature_f=88.0,
            uv_index=9.0,
            rain_pattern_inches=(0.0, 0.16, 0.0, 0.14, 0.0, 0.0, 0.10),
            forecast_amount_inches=0.45,
            forecast_probability=69.0,
            note="Forecast just below the rain-probability threshold should not defer.",
        ),
        ScenarioSpec(
            name="Seattle July shaded fixed spray",
            now_local=seattle_summer,
            latitude=47.61,
            longitude=-122.33,
            temperature_f=75.0,
            uv_index=6.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            zone_overrides={
                1: {
                    "exposure_type": "SOME_SHADE",
                    "nozzle_type": "FIXED_SPRAY_HEAD",
                    "smart_duration": 1200,
                }
            },
            note="Shaded fixed-spray cool-season turf should run below the default Seattle rotary/full-sun case.",
        ),
        ScenarioSpec(
            name="Salt Lake spring threshold with weekly cap",
            now_local=_dt_for_zone(4, 20, 6, 0, TZ_DENVER),
            latitude=40.76,
            longitude=-111.89,
            temperature_f=63.0,
            uv_index=6.0,
            rain_pattern_inches=(0.0, 0.0, 0.10, 0.05, 0.0, 0.0, 0.08),
            minimum_run_threshold_minutes=10,
            zone_runtime_banks={3: 8},
            max_weekly_runtime_minutes={3: 5},
            note="Remaining weekly cap below the threshold should not force an odd partial run.",
        ),
        ScenarioSpec(
            name="Salt Lake April wind at hold threshold",
            now_local=_dt_for_zone(4, 20, 6, 0, TZ_DENVER),
            latitude=40.76,
            longitude=-111.89,
            temperature_f=63.0,
            uv_index=6.0,
            wind_speed_mph=12.0,
            rain_pattern_inches=(0.0, 0.0, 0.10, 0.05, 0.0, 0.0, 0.08),
            minimum_run_threshold_minutes=0,
            zone_sprinkler_wind_profiles={
                1: "Standard spray",
                2: "Standard spray",
                3: "Standard spray",
            },
            note="Wind exactly at the configured hold threshold should still pause watering.",
        ),
        ScenarioSpec(
            name="Salt Lake April wind just below hold threshold",
            now_local=_dt_for_zone(4, 20, 6, 0, TZ_DENVER),
            latitude=40.76,
            longitude=-111.89,
            temperature_f=63.0,
            uv_index=6.0,
            wind_speed_mph=11.9,
            rain_pattern_inches=(0.0, 0.0, 0.10, 0.05, 0.0, 0.0, 0.08),
            minimum_run_threshold_minutes=0,
            zone_sprinkler_wind_profiles={
                1: "Standard spray",
                2: "Standard spray",
                3: "Standard spray",
            },
            note="Wind just below the configured hold threshold should not pause watering.",
        ),
        ScenarioSpec(
            name="Salt Lake March temperature at hold threshold",
            now_local=_dt_for_zone(3, 19, 6, 15, TZ_DENVER),
            latitude=40.76,
            longitude=-111.89,
            temperature_f=40.0,
            uv_index=3.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            note="Temperature exactly at the configured minimum should still weather-hold watering.",
        ),
        ScenarioSpec(
            name="Salt Lake March temperature just above hold threshold",
            now_local=_dt_for_zone(3, 19, 6, 15, TZ_DENVER),
            latitude=40.76,
            longitude=-111.89,
            temperature_f=41.0,
            uv_index=3.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            note="Temperature just above the configured minimum should allow normal planning.",
        ),
        ScenarioSpec(
            name="Salt Lake April combined cold and wind hold",
            now_local=_dt_for_zone(4, 20, 6, 0, TZ_DENVER),
            latitude=40.76,
            longitude=-111.89,
            temperature_f=38.0,
            uv_index=5.0,
            wind_speed_mph=18.0,
            rain_pattern_inches=(0.0, 0.0, 0.10, 0.05, 0.0, 0.0, 0.08),
            note="Cold and windy mornings together should still produce one weather-hold decision and bank runtime.",
        ),
        ScenarioSpec(
            name="Salt Lake July windy hold existing bank does not stack",
            now_local=_dt_for_zone(7, 15, 5, 30, TZ_DENVER),
            latitude=40.76,
            longitude=-111.89,
            temperature_f=95.0,
            uv_index=10.0,
            wind_speed_mph=18.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            zone_runtime_banks={1: 20},
            note="A second windy hold should keep the bank at the current required runtime, not add on top of it.",
        ),
        ScenarioSpec(
            name="Salt Lake July windy hold respects weekly cap",
            now_local=_dt_for_zone(7, 15, 5, 30, TZ_DENVER),
            latitude=40.76,
            longitude=-111.89,
            temperature_f=95.0,
            uv_index=10.0,
            wind_speed_mph=18.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            max_weekly_runtime_minutes={1: 30},
            note="A weather-held day should only bank up to the remaining weekly cap.",
        ),
        ScenarioSpec(
            name="Salt Lake April calm weather bank respects weekly cap",
            now_local=_dt_for_zone(4, 21, 6, 0, TZ_DENVER),
            latitude=40.76,
            longitude=-111.89,
            temperature_f=64.0,
            uv_index=6.0,
            rain_pattern_inches=(0.0, 0.10, 0.05, 0.0, 0.0, 0.08, 0.0),
            zone_runtime_banks={1: 22},
            max_weekly_runtime_minutes={1: 15},
            note="When a weather-held bank is released on a calm day, the weekly cap should still limit what runs.",
        ),
        ScenarioSpec(
            name="Salt Lake April rain delay outranks weather hold",
            now_local=_dt_for_zone(4, 21, 6, 0, TZ_DENVER),
            latitude=40.76,
            longitude=-111.89,
            temperature_f=38.0,
            uv_index=4.0,
            wind_speed_mph=18.0,
            rain_pattern_inches=(0.0, 0.10, 0.05, 0.0, 0.0, 0.08, 0.80),
            zone_runtime_banks={1: 22},
            note="Rain delay should outrank a simultaneous cold or windy morning and clear any banked runtime.",
        ),
    )


def _scenario_specs() -> tuple[ScenarioSpec, ...]:
    """Return the full validation scenario matrix."""

    slc_april_20 = _dt_for_zone(4, 20, 6, 0, TZ_DENVER)
    slc_april_21 = _dt_for_zone(4, 21, 6, 0, TZ_DENVER)
    slc_march_18 = _dt_for_zone(3, 18, 6, 15, TZ_DENVER)
    slc_march_19 = _dt_for_zone(3, 19, 6, 15, TZ_DENVER)
    slc_may_18 = _dt_for_zone(5, 18, 6, 0, TZ_DENVER)
    slc_july_15 = _dt_for_zone(7, 15, 5, 30, TZ_DENVER)
    slc_july_16 = _dt_for_zone(7, 16, 5, 30, TZ_DENVER)
    slc_april_20_late = _dt_for_zone(4, 20, 10, 0, TZ_DENVER)
    st_george_june_15 = _dt_for_zone(6, 15, 5, 30, TZ_DENVER)
    phoenix_aug_15 = _dt_for_zone(8, 15, 4, 30, TZ_PHOENIX)
    sacramento_july_16 = _dt_for_zone(7, 16, 5, 15, TZ_LOS_ANGELES)
    san_diego_may_18 = _dt_for_zone(5, 18, 5, 45, TZ_LOS_ANGELES)
    seattle_july_15 = _dt_for_zone(7, 15, 5, 30, TZ_LOS_ANGELES)
    seattle_march_20 = _dt_for_zone(3, 20, 6, 30, TZ_LOS_ANGELES)
    denver_july_15 = _dt_for_zone(7, 15, 5, 20, TZ_DENVER)
    minneapolis_may_20 = _dt_for_zone(5, 20, 6, 0, TZ_CHICAGO)
    boston_july_17 = _dt_for_zone(7, 17, 5, 20, TZ_NEW_YORK)
    minneapolis_jan_15 = _dt_for_zone(1, 15, 8, 0, TZ_CHICAGO)
    atlanta_july_15 = _dt_for_zone(7, 15, 5, 30, TZ_NEW_YORK)
    houston_june_21 = _dt_for_zone(6, 21, 5, 25, TZ_CHICAGO)
    atlanta_aug_12 = _dt_for_zone(8, 12, 5, 30, TZ_NEW_YORK)
    miami_july_18 = _dt_for_zone(7, 18, 5, 45, TZ_NEW_YORK)

    base_scenarios = (
        ScenarioSpec(
            name="Salt Lake July hot dry",
            now_local=slc_july_15,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=95.0,
            uv_index=10.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            note="Reference hot interior-mountain summer week.",
        ),
        ScenarioSpec(
            name="Salt Lake July light daily rain",
            now_local=slc_july_15,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=88.0,
            uv_index=9.0,
            rain_pattern_inches=(0.14, 0.14, 0.14, 0.14, 0.14, 0.14, 0.14),
            note="Frequent light summer rain should reduce more than a single soaker.",
        ),
        ScenarioSpec(
            name="Salt Lake July trace drizzle",
            now_local=slc_july_15,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=88.0,
            uv_index=9.0,
            rain_pattern_inches=(0.08, 0.08, 0.08, 0.08, 0.08, 0.08, 0.08),
            note="Sub-threshold drizzle should not buy much effective-rain credit.",
        ),
        ScenarioSpec(
            name="Salt Lake July single soaker",
            now_local=slc_july_15,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=88.0,
            uv_index=9.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0),
            note="Single storm should get less effective credit than daily light rain.",
        ),
        ScenarioSpec(
            name="Salt Lake April shoulder",
            now_local=slc_april_20,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=63.0,
            uv_index=6.0,
            rain_pattern_inches=(0.0, 0.0, 0.10, 0.05, 0.0, 0.0, 0.08),
            forecast_amount_inches=0.25,
            forecast_probability=60.0,
            note="Shoulder season should stay materially lower than midsummer.",
        ),
        ScenarioSpec(
            name="Salt Lake April reduced grass coefficient",
            now_local=slc_april_20,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=63.0,
            uv_index=6.0,
            rain_pattern_inches=(0.0, 0.0, 0.10, 0.05, 0.0, 0.0, 0.08),
            forecast_amount_inches=0.25,
            forecast_probability=60.0,
            zone_watering_coefficients={1: 0.9},
            note="Lowering a zone coefficient should lower both demand and runtime instead of being cancelled by higher next-day debt.",
        ),
        ScenarioSpec(
            name="Salt Lake April follow-up baseline after run",
            now_local=slc_april_21,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=64.0,
            uv_index=6.0,
            rain_pattern_inches=(0.0, 0.10, 0.05, 0.0, 0.0, 0.08, 0.0),
            minimum_run_threshold_minutes=0,
            recent_events=(
                RecentEventSpec(zone_number=1, days_ago=1, duration_minutes=17),
            ),
            note="Baseline next-day follow-up after a normal spring grass run.",
        ),
        ScenarioSpec(
            name="Salt Lake April follow-up reduced coefficient after run",
            now_local=slc_april_21,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=64.0,
            uv_index=6.0,
            rain_pattern_inches=(0.0, 0.10, 0.05, 0.0, 0.0, 0.08, 0.0),
            minimum_run_threshold_minutes=0,
            zone_watering_coefficients={1: 0.9},
            recent_events=(
                RecentEventSpec(zone_number=1, days_ago=1, duration_minutes=14),
            ),
            note="Reduced-coefficient next-day follow-up should still stay below the baseline instead of rebounding upward.",
        ),
        ScenarioSpec(
            name="Salt Lake April threshold crosses cleanly",
            now_local=slc_april_20,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=63.0,
            uv_index=6.0,
            rain_pattern_inches=(0.0, 0.0, 0.10, 0.05, 0.0, 0.0, 0.08),
            forecast_amount_inches=0.25,
            forecast_probability=60.0,
            minimum_run_threshold_minutes=10,
            zone_runtime_banks={3: 8},
            zone_overrides={3: {"soil_type": "SANDY_LOAM"}},
            note="A banked spring zone should cross the threshold cleanly without adding old minutes on top of today's demand.",
        ),
        ScenarioSpec(
            name="Salt Lake April xeric drip cool spacing",
            now_local=slc_april_20,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=58.0,
            uv_index=6.0,
            rain_pattern_inches=(0.0, 0.0, 0.10, 0.05, 0.0, 0.0, 0.08),
            zone_profiles={2: "Drought tolerant"},
            recent_events=(
                RecentEventSpec(zone_number=2, days_ago=3, duration_minutes=45),
            ),
            note="Cool-spring drought-tolerant drip zones should still space out deeply instead of nibbling frequently.",
        ),
        ScenarioSpec(
            name="Salt Lake March cool start",
            now_local=slc_march_18,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=52.0,
            uv_index=4.0,
            rain_pattern_inches=(0.0, 0.0, 0.12, 0.0, 0.0, 0.06, 0.0),
            note="Very early season should stay restrained.",
        ),
        ScenarioSpec(
            name="Salt Lake March warm dry startup",
            now_local=slc_march_19,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=68.0,
            uv_index=6.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.10, 0.0, 0.0, 0.0),
            forecast_amount_inches=0.10,
            forecast_probability=35.0,
            note="Anomalously warm, low-snowpack March startup check for Salt Lake City.",
        ),
        ScenarioSpec(
            name="Salt Lake March threshold carryover floor",
            now_local=slc_march_19,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=52.0,
            uv_index=4.0,
            rain_pattern_inches=(0.0, 0.0, 0.12, 0.0, 0.0, 0.06, 0.0),
            minimum_run_threshold_minutes=10,
            zone_runtime_banks={3: 8},
            note="Carryover should preserve prior sub-threshold demand without double counting it.",
        ),
        ScenarioSpec(
            name="Salt Lake March threshold force after 7 days",
            now_local=slc_march_19,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=52.0,
            uv_index=4.0,
            rain_pattern_inches=(0.0, 0.0, 0.12, 0.0, 0.0, 0.06, 0.0),
            minimum_run_threshold_minutes=10,
            zone_runtime_banks={3: 8},
            recent_events=(
                RecentEventSpec(zone_number=3, days_ago=8, duration_minutes=12),
            ),
            note="Zones should still run after 7 days even if the bank is still below threshold.",
        ),
        ScenarioSpec(
            name="Salt Lake March threshold cleared by rain",
            now_local=slc_march_19,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=52.0,
            uv_index=4.0,
            rain_pattern_inches=(0.0, 0.0, 0.12, 0.0, 0.0, 0.06, 0.45),
            minimum_run_threshold_minutes=10,
            zone_runtime_banks={3: 8},
            note="Meaningful rain should clear any banked sub-threshold carryover.",
        ),
        ScenarioSpec(
            name="Salt Lake April no application rates configured",
            now_local=slc_april_20,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=63.0,
            uv_index=6.0,
            rain_pattern_inches=(0.0, 0.0, 0.10, 0.05, 0.0, 0.0, 0.08),
            zone_application_rates={
                1: 0.0,
                2: 0.0,
                3: 0.0,
            },
            note="If no enabled zones have a measured application rate yet, the controller should stay in a not-configured monitoring state.",
        ),
        ScenarioSpec(
            name="Salt Lake April partial application rates configured",
            now_local=slc_april_20,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=63.0,
            uv_index=6.0,
            rain_pattern_inches=(0.0, 0.0, 0.10, 0.05, 0.0, 0.0, 0.08),
            zone_application_rates={
                1: 0.60,
                2: 0.0,
                3: 1.00,
            },
            minimum_run_threshold_minutes=0,
            note="Configured zones should still plan normally when another enabled zone has not been calibrated yet.",
        ),
        ScenarioSpec(
            name="Salt Lake April windy hold banks demand",
            now_local=slc_april_20,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=63.0,
            uv_index=6.0,
            wind_speed_mph=18.0,
            rain_pattern_inches=(0.0, 0.0, 0.10, 0.05, 0.0, 0.0, 0.08),
            note="A windy shoulder-season morning should weather-hold watering and bank the missed runtime.",
        ),
        ScenarioSpec(
            name="Salt Lake April gust hold banks demand",
            now_local=slc_april_20,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=63.0,
            uv_index=6.0,
            wind_speed_mph=5.0,
            wind_gust_mph=19.0,
            rain_pattern_inches=(0.0, 0.0, 0.10, 0.05, 0.0, 0.0, 0.08),
            minimum_run_threshold_minutes=0,
            zone_sprinkler_wind_profiles={
                1: "Standard spray",
                2: "Standard spray",
                3: "Standard spray",
            },
            note="A gust-only storm should still hold watering for spray-style distribution without changing ET math.",
        ),
        ScenarioSpec(
            name="Salt Lake April rotary wind profile allows breezy run",
            now_local=slc_april_20,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=63.0,
            uv_index=6.0,
            wind_speed_mph=14.0,
            rain_pattern_inches=(0.0, 0.0, 0.10, 0.05, 0.0, 0.0, 0.08),
            zone_sprinkler_wind_profiles={1: "Rotary / stream"},
            note="Rotary/stream zones should tolerate modestly higher sustained wind than fixed spray mode.",
        ),
        ScenarioSpec(
            name="Salt Lake April drip wind profile ignores windy morning",
            now_local=slc_april_20,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=63.0,
            uv_index=6.0,
            wind_speed_mph=20.0,
            wind_gust_mph=28.0,
            rain_pattern_inches=(0.0, 0.0, 0.10, 0.05, 0.0, 0.0, 0.08),
            minimum_run_threshold_minutes=0,
            zone_sprinkler_wind_profiles={2: "Drip / bubbler"},
            note="Drip/bubbler zones should not be wind-held even during very breezy mornings.",
        ),
        ScenarioSpec(
            name="Salt Lake April mixed zone wind profiles",
            now_local=slc_april_20,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=63.0,
            uv_index=6.0,
            wind_speed_mph=14.0,
            rain_pattern_inches=(0.0, 0.0, 0.10, 0.05, 0.0, 0.0, 0.08),
            minimum_run_threshold_minutes=0,
            zone_sprinkler_wind_profiles={
                1: "Rotary / stream",
                2: "Drip / bubbler",
            },
            note="Mixed per-zone wind profiles should let rotary and drip zones run while standard spray still holds.",
        ),
        ScenarioSpec(
            name="Salt Lake April rotary gust profile allows breezy run",
            now_local=slc_april_20,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=63.0,
            uv_index=6.0,
            wind_speed_mph=5.0,
            wind_gust_mph=21.0,
            rain_pattern_inches=(0.0, 0.0, 0.10, 0.05, 0.0, 0.0, 0.08),
            zone_sprinkler_wind_profiles={1: "Rotary / stream"},
            note="Rotary/stream zones should also tolerate moderate gusts that would stop standard spray.",
        ),
        ScenarioSpec(
            name="Salt Lake April persisted wind stop hold",
            now_local=slc_april_20_late,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=66.0,
            uv_index=6.0,
            wind_speed_mph=3.0,
            rain_pattern_inches=(0.0, 0.0, 0.10, 0.05, 0.0, 0.0, 0.08),
            zone_weather_stop_holds={
                1: {
                    "date": slc_april_20.date().isoformat(),
                    "reason": "Watering for this zone was already stopped earlier today because of wind.",
                }
            },
            note="A zone stopped mid-cycle for wind should stay banked for the rest of the local day.",
        ),
        ScenarioSpec(
            name="Salt Lake March cold overrides drip wind profile",
            now_local=slc_march_19,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=36.0,
            uv_index=4.0,
            wind_speed_mph=20.0,
            wind_gust_mph=28.0,
            minimum_run_threshold_minutes=0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            zone_sprinkler_wind_profiles={2: "Drip / bubbler"},
            note="Drip/bubbler should ignore wind, but not bypass the global cold-weather hold.",
        ),
        ScenarioSpec(
            name="Salt Lake March cold hold banks demand",
            now_local=slc_march_19,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=34.0,
            uv_index=3.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            note="Cold mornings should weather-hold watering and bank the missed runtime.",
        ),
        ScenarioSpec(
            name="Salt Lake April calm after wind bank",
            now_local=slc_april_21,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=64.0,
            uv_index=6.0,
            rain_pattern_inches=(0.0, 0.10, 0.05, 0.0, 0.0, 0.08, 0.0),
            zone_runtime_banks={1: 22},
            note="A prior weather-held runtime should act as a floor on the next calm day without double counting.",
        ),
        ScenarioSpec(
            name="Salt Lake April rain clears weather bank",
            now_local=slc_april_21,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=60.0,
            uv_index=5.0,
            rain_pattern_inches=(0.0, 0.10, 0.05, 0.0, 0.0, 0.08, 0.45),
            zone_runtime_banks={1: 22},
            note="If it rains after a weather hold, the missed runtime bank should clear instead of forcing a make-up run.",
        ),
        ScenarioSpec(
            name="Salt Lake July dry windy ET boost",
            now_local=slc_july_15,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=95.0,
            uv_index=10.0,
            humidity_percent=18.0,
            wind_speed_mph=16.0,
            max_watering_wind_speed_mph=25.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            note="Dry windy air should raise ET noticeably without tripping the wind-hold guard rail when the threshold is intentionally relaxed.",
        ),
        ScenarioSpec(
            name="Salt Lake July humid calm ET suppress",
            now_local=slc_july_15,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=95.0,
            uv_index=10.0,
            humidity_percent=70.0,
            wind_speed_mph=2.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            note="Humid calm air should soften ET and the resulting deficit without changing the overall summer pattern.",
        ),
        ScenarioSpec(
            name="Salt Lake March sandy garden releases earlier",
            now_local=slc_march_19,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=52.0,
            uv_index=4.0,
            rain_pattern_inches=(0.0, 0.0, 0.12, 0.0, 0.0, 0.06, 0.0),
            minimum_run_threshold_minutes=10,
            zone_runtime_banks={3: 10},
            zone_overrides={
                3: {
                    "available_water_capacity": 0.08,
                    "soil_type": "SANDY_LOAM",
                }
            },
            note="Lower-storage sandy raised beds should cross the minimum-run floor sooner than clay-heavy beds.",
        ),
        ScenarioSpec(
            name="Salt Lake March clay garden holds longer",
            now_local=slc_march_19,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=52.0,
            uv_index=4.0,
            rain_pattern_inches=(0.0, 0.0, 0.12, 0.0, 0.0, 0.06, 0.0),
            minimum_run_threshold_minutes=10,
            zone_runtime_banks={3: 10},
            zone_overrides={
                3: {
                    "available_water_capacity": 0.20,
                    "soil_type": "CLAY_LOAM",
                }
            },
            note="Higher-storage clay-heavy beds should bank longer before a short early-spring run is worthwhile.",
        ),
        ScenarioSpec(
            name="Salt Lake rain delay carry-over",
            now_local=slc_may_18,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=70.0,
            uv_index=7.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.80),
            note="Recent soaking rain should create a rain-delay hold.",
        ),
        ScenarioSpec(
            name="Salt Lake weekly cap reached",
            now_local=slc_july_16,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=96.0,
            uv_index=10.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            max_weekly_runtime_minutes={1: 60},
            recent_events=(
                RecentEventSpec(zone_number=1, days_ago=2, duration_minutes=50),
            ),
            note="Grass zone should respect remaining weekly runtime.",
        ),
        ScenarioSpec(
            name="Salt Lake July xeric drip spacing",
            now_local=slc_july_16,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=95.0,
            uv_index=10.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            zone_profiles={2: "Drought tolerant"},
            recent_events=(
                RecentEventSpec(zone_number=2, days_ago=2, duration_minutes=60),
            ),
            note="Water-efficient drip beds should skip between deeper irrigation events.",
        ),
        ScenarioSpec(
            name="Salt Lake July xeric drip short test",
            now_local=slc_july_16,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=95.0,
            uv_index=10.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            zone_profiles={2: "Drought tolerant"},
            recent_events=(
                RecentEventSpec(zone_number=2, days_ago=1, duration_minutes=3),
            ),
            note="Very short manual test runs should not block drought-tolerant interval spacing.",
        ),
        ScenarioSpec(
            name="Salt Lake July raised-bed vegetables",
            now_local=slc_july_16,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=95.0,
            uv_index=10.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            zone_profiles={3: "Vegetable garden"},
            recent_events=(
                RecentEventSpec(zone_number=3, days_ago=1, duration_minutes=15),
            ),
            note="Raised-bed vegetables should stay on a frequent short-cycle rhythm in summer.",
        ),
        ScenarioSpec(
            name="Salt Lake July raised-bed vegetables due",
            now_local=slc_july_16,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=95.0,
            uv_index=10.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            zone_profiles={3: "Vegetable garden"},
            recent_events=(
                RecentEventSpec(zone_number=3, days_ago=2, duration_minutes=15),
            ),
            note="Raised-bed vegetables should run again quickly, but still cap each summer session.",
        ),
        ScenarioSpec(
            name="Salt Lake July profile compare default",
            now_local=slc_july_16,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=95.0,
            uv_index=10.0,
            humidity_percent=24.0,
            wind_speed_mph=8.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            note="Default profile baseline for direct per-zone deficit comparison.",
        ),
        ScenarioSpec(
            name="Salt Lake July profile compare drought",
            now_local=slc_july_16,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=95.0,
            uv_index=10.0,
            humidity_percent=24.0,
            wind_speed_mph=8.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            zone_profiles={2: "Drought tolerant"},
            note="Drought-tolerant profile should accumulate the least zone deficit under matching conditions.",
        ),
        ScenarioSpec(
            name="Salt Lake July profile compare trees",
            now_local=slc_july_16,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=95.0,
            uv_index=10.0,
            humidity_percent=24.0,
            wind_speed_mph=8.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            zone_profiles={2: "Trees / shrubs"},
            note="Trees / shrubs profile should sit between lawn and drought-tolerant demand under matching conditions.",
        ),
        ScenarioSpec(
            name="Salt Lake July profile compare vegetable",
            now_local=slc_july_16,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=95.0,
            uv_index=10.0,
            humidity_percent=24.0,
            wind_speed_mph=8.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            zone_profiles={2: "Vegetable garden"},
            note="Vegetable-garden profile should accumulate the most zone deficit under matching conditions.",
        ),
        ScenarioSpec(
            name="Salt Lake July trees shrubs recent spacing",
            now_local=slc_july_16,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=95.0,
            uv_index=10.0,
            humidity_percent=24.0,
            wind_speed_mph=8.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            zone_profiles={2: "Trees / shrubs"},
            recent_events=(
                RecentEventSpec(zone_number=2, days_ago=2, duration_minutes=60),
            ),
            note="Trees / shrubs profile should space out after a recent deeper run.",
        ),
        ScenarioSpec(
            name="Salt Lake July one-hour manual window garden first",
            now_local=slc_july_16,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=95.0,
            uv_index=10.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            use_automatic_window=False,
            manual_window_start=dt_time(5, 30),
            manual_window_end=dt_time(6, 30),
            note="A tight one-hour manual window should schedule the highest-priority due zone only.",
        ),
        ScenarioSpec(
            name="Salt Lake July one-hour manual window grass rotates in",
            now_local=slc_july_16,
            latitude=40.76,
            longitude=-111.89,
            temperature_f=95.0,
            uv_index=10.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            use_automatic_window=False,
            manual_window_start=dt_time(5, 30),
            manual_window_end=dt_time(6, 30),
            recent_events=(
                RecentEventSpec(zone_number=3, days_ago=1, duration_minutes=20),
            ),
            note="If the garden just ran, the grass zone should rotate into the short manual window next.",
        ),
        ScenarioSpec(
            name="St George June desert heat",
            now_local=st_george_june_15,
            latitude=37.10,
            longitude=-113.58,
            temperature_f=104.0,
            uv_index=11.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            note="Hot desert shoulder-season week should push strong demand.",
        ),
        ScenarioSpec(
            name="Phoenix August monsoon watch",
            now_local=phoenix_aug_15,
            latitude=33.45,
            longitude=-112.07,
            temperature_f=105.0,
            uv_index=10.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            forecast_amount_inches=0.35,
            forecast_probability=70.0,
            note="Extreme desert heat should still water despite monsoon chance if deficit is high.",
        ),
        ScenarioSpec(
            name="Sacramento July inland heat",
            now_local=sacramento_july_16,
            latitude=38.58,
            longitude=-121.49,
            temperature_f=99.0,
            uv_index=10.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            note="Interior California heat should sit above marine climates but below desert extremes.",
        ),
        ScenarioSpec(
            name="San Diego May marine mild",
            now_local=san_diego_may_18,
            latitude=32.72,
            longitude=-117.16,
            temperature_f=69.0,
            uv_index=7.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            note="Coastal Southern California spring should water, but stay conservative.",
        ),
        ScenarioSpec(
            name="Seattle July mild dry",
            now_local=seattle_july_15,
            latitude=47.61,
            longitude=-122.33,
            temperature_f=76.0,
            uv_index=7.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            forecast_amount_inches=0.10,
            forecast_probability=40.0,
            note="Marine summer should run, but less aggressively than Utah/desert heat.",
        ),
        ScenarioSpec(
            name="Seattle March wet cool",
            now_local=seattle_march_20,
            latitude=47.61,
            longitude=-122.33,
            temperature_f=49.0,
            uv_index=3.0,
            rain_pattern_inches=(0.18, 0.22, 0.0, 0.15, 0.18, 0.10, 0.16),
            note="Cool wet spring should not trigger meaningful watering.",
        ),
        ScenarioSpec(
            name="Denver July high-plains dry",
            now_local=denver_july_15,
            latitude=39.74,
            longitude=-104.99,
            temperature_f=92.0,
            uv_index=10.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.10, 0.0, 0.0, 0.0),
            note="High-plains interior should sit near Utah but below the hottest desert cases.",
        ),
        ScenarioSpec(
            name="Minneapolis May cool spring",
            now_local=minneapolis_may_20,
            latitude=44.98,
            longitude=-93.26,
            temperature_f=60.0,
            uv_index=5.0,
            rain_pattern_inches=(0.12, 0.0, 0.0, 0.08, 0.0, 0.14, 0.0),
            note="Cool northern spring should remain conservative.",
        ),
        ScenarioSpec(
            name="Boston July humid summer",
            now_local=boston_july_17,
            latitude=42.36,
            longitude=-71.06,
            temperature_f=84.0,
            uv_index=8.0,
            rain_pattern_inches=(0.0, 0.0, 0.12, 0.0, 0.0, 0.0, 0.0),
            note="Northeast summer should run, but stay below Utah interior heat.",
        ),
        ScenarioSpec(
            name="Minneapolis January dormant",
            now_local=minneapolis_jan_15,
            latitude=44.98,
            longitude=-93.26,
            temperature_f=18.0,
            uv_index=1.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            note="Dormant-season northern lawns should effectively skip.",
        ),
        ScenarioSpec(
            name="Atlanta July stormy",
            now_local=atlanta_july_15,
            latitude=33.75,
            longitude=-84.39,
            temperature_f=89.0,
            uv_index=9.0,
            rain_pattern_inches=(0.0, 0.18, 0.0, 0.22, 0.0, 0.0, 0.18),
            forecast_amount_inches=0.55,
            forecast_probability=75.0,
            note="Humid storm-prone Southeast should defer when rain is highly likely.",
        ),
        ScenarioSpec(
            name="Houston June stormy",
            now_local=houston_june_21,
            latitude=29.76,
            longitude=-95.37,
            temperature_f=88.0,
            uv_index=8.0,
            humidity_percent=82.0,
            wind_speed_mph=6.0,
            rain_pattern_inches=(0.24, 0.10, 0.22, 0.0, 0.28, 0.18, 0.16),
            forecast_amount_inches=0.85,
            forecast_probability=90.0,
            note="Gulf Coast storm pattern should defer when tomorrow's rain is both likely and meaningful.",
        ),
        ScenarioSpec(
            name="Atlanta August dry streak",
            now_local=atlanta_aug_12,
            latitude=33.75,
            longitude=-84.39,
            temperature_f=93.0,
            uv_index=9.0,
            rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            note="Southeast should still water during a real hot dry stretch.",
        ),
        ScenarioSpec(
            name="Miami July tropical wet",
            now_local=miami_july_18,
            latitude=25.76,
            longitude=-80.19,
            temperature_f=91.0,
            uv_index=10.0,
            rain_pattern_inches=(0.35, 0.0, 0.42, 0.0, 0.18, 0.50, 0.0),
            forecast_amount_inches=0.60,
            forecast_probability=80.0,
            note="Tropical wet pattern should usually defer or stay very low.",
        ),
    )

    return base_scenarios + _generated_matrix_scenarios() + _targeted_edge_scenarios()


def _validate(results: dict[str, object]) -> list[str]:
    """Return a list of expectation failures."""

    failures: list[str] = []

    slc_hot = results["Salt Lake July hot dry"]
    slc_light = results["Salt Lake July light daily rain"]
    slc_drizzle = results["Salt Lake July trace drizzle"]
    slc_soaker = results["Salt Lake July single soaker"]
    slc_april = results["Salt Lake April shoulder"]
    slc_april_reduced_grass = results["Salt Lake April reduced grass coefficient"]
    slc_april_follow_up = results["Salt Lake April follow-up baseline after run"]
    slc_april_follow_up_reduced = results["Salt Lake April follow-up reduced coefficient after run"]
    slc_april_threshold = results["Salt Lake April threshold crosses cleanly"]
    slc_april_xeric = results["Salt Lake April xeric drip cool spacing"]
    slc_march = results["Salt Lake March cool start"]
    slc_march_warm = results["Salt Lake March warm dry startup"]
    slc_threshold_floor = results["Salt Lake March threshold carryover floor"]
    slc_threshold_force = results["Salt Lake March threshold force after 7 days"]
    slc_threshold_rain = results["Salt Lake March threshold cleared by rain"]
    slc_windy_hold = results["Salt Lake April windy hold banks demand"]
    slc_cold_hold = results["Salt Lake March cold hold banks demand"]
    slc_calm_after_wind_bank = results["Salt Lake April calm after wind bank"]
    slc_rain_clears_weather_bank = results["Salt Lake April rain clears weather bank"]
    slc_dry_windy = results["Salt Lake July dry windy ET boost"]
    slc_humid_calm = results["Salt Lake July humid calm ET suppress"]
    slc_sandy_garden = results["Salt Lake March sandy garden releases earlier"]
    slc_clay_garden = results["Salt Lake March clay garden holds longer"]
    slc_delay = results["Salt Lake rain delay carry-over"]
    slc_cap = results["Salt Lake weekly cap reached"]
    slc_xeric = results["Salt Lake July xeric drip spacing"]
    slc_xeric_short = results["Salt Lake July xeric drip short test"]
    slc_vegetables = results["Salt Lake July raised-bed vegetables"]
    slc_vegetables_due = results["Salt Lake July raised-bed vegetables due"]
    slc_profile_default = results["Salt Lake July profile compare default"]
    slc_profile_drought = results["Salt Lake July profile compare drought"]
    slc_profile_trees = results["Salt Lake July profile compare trees"]
    slc_profile_vegetable = results["Salt Lake July profile compare vegetable"]
    slc_trees_recent = results["Salt Lake July trees shrubs recent spacing"]
    slc_manual_window = results["Salt Lake July one-hour manual window garden first"]
    slc_manual_window_rotated = results["Salt Lake July one-hour manual window grass rotates in"]
    stg = results["St George June desert heat"]
    phx = results["Phoenix August monsoon watch"]
    sac = results["Sacramento July inland heat"]
    sdc = results["San Diego May marine mild"]
    sea = results["Seattle July mild dry"]
    sea_wet = results["Seattle March wet cool"]
    den = results["Denver July high-plains dry"]
    msp_may = results["Minneapolis May cool spring"]
    bos = results["Boston July humid summer"]
    msp_jan = results["Minneapolis January dormant"]
    atl_storm = results["Atlanta July stormy"]
    hou = results["Houston June stormy"]
    atl_dry = results["Atlanta August dry streak"]
    mia = results["Miami July tropical wet"]
    dallas_spring_warm = results["Dallas April warm-season turf dry"]
    dallas_winter_warm = results["Dallas January warm-season dormant"]
    dallas_spring_wet = results["Dallas April warm-season turf wet"]
    dallas_summer_warm = results["Dallas July warm-season turf dry"]
    dallas_fall_warm = results["Dallas October warm-season turf taper"]
    dallas_shaded_warm = results["Dallas July warm-season shaded fixed spray"]
    chicago_forecast_below_amount = results["Chicago July forecast below defer amount"]
    chicago_forecast_at_threshold = results["Chicago July forecast at defer threshold"]
    chicago_forecast_below_probability = results["Chicago July forecast below defer probability"]
    seattle_shaded_spray = results["Seattle July shaded fixed spray"]
    slc_threshold_cap = results["Salt Lake spring threshold with weekly cap"]
    slc_no_application_rates = results["Salt Lake April no application rates configured"]
    slc_partial_application_rates = results["Salt Lake April partial application rates configured"]
    slc_wind_at_threshold = results["Salt Lake April wind at hold threshold"]
    slc_wind_below_threshold = results["Salt Lake April wind just below hold threshold"]
    slc_gust_hold = results["Salt Lake April gust hold banks demand"]
    slc_rotary_breezy = results["Salt Lake April rotary wind profile allows breezy run"]
    slc_drip_breezy = results["Salt Lake April drip wind profile ignores windy morning"]
    slc_mixed_wind_profiles = results["Salt Lake April mixed zone wind profiles"]
    slc_rotary_gust_breezy = results["Salt Lake April rotary gust profile allows breezy run"]
    slc_persisted_wind_stop = results["Salt Lake April persisted wind stop hold"]
    slc_cold_overrides_drip = results["Salt Lake March cold overrides drip wind profile"]
    slc_temp_at_threshold = results["Salt Lake March temperature at hold threshold"]
    slc_temp_above_threshold = results["Salt Lake March temperature just above hold threshold"]
    slc_combined_weather_hold = results["Salt Lake April combined cold and wind hold"]
    slc_windy_bank_no_stack = results["Salt Lake July windy hold existing bank does not stack"]
    slc_windy_cap_hold = results["Salt Lake July windy hold respects weekly cap"]
    slc_calm_bank_weekly_cap = results["Salt Lake April calm weather bank respects weekly cap"]
    slc_rain_delay_over_weather = results["Salt Lake April rain delay outranks weather hold"]

    if _zone_runtime(slc_hot, "Backyard Right") <= _zone_runtime(slc_april, "Backyard Right"):
        failures.append("Salt Lake midsummer grass should exceed April shoulder runtime.")
    if _zone_runtime(slc_hot, "Garden") <= _zone_runtime(slc_april, "Garden"):
        failures.append("Salt Lake midsummer garden should exceed April shoulder runtime.")
    if _zone_runtime(slc_april, "Backyard Right") > 30:
        failures.append("Salt Lake April grass is still too aggressive for a shoulder-season guard rail.")
    if _zone_runtime(slc_april, "Front Yard Perennials") > 45:
        failures.append("Salt Lake April perennials should stay below a moderate drip runtime.")
    if _zone_plan(slc_april_reduced_grass, "Backyard Right").user_watering_coefficient != 0.9:
        failures.append("Per-zone watering coefficient should be preserved on the zone plan.")
    if _zone_plan(slc_april_reduced_grass, "Backyard Right").zone_demand_multiplier >= _zone_plan(
        slc_april,
        "Backyard Right",
    ).zone_demand_multiplier:
        failures.append("Lowering a zone coefficient should lower that zone's modeled demand multiplier.")
    if _zone_plan(slc_april_reduced_grass, "Backyard Right").deficit_inches >= _zone_plan(
        slc_april,
        "Backyard Right",
    ).deficit_inches:
        failures.append("Lowering a zone coefficient should lower that zone's deficit, not leave it unchanged.")
    if _zone_runtime(slc_april_reduced_grass, "Backyard Right") >= _zone_runtime(
        slc_april,
        "Backyard Right",
    ):
        failures.append("Lowering a zone coefficient should lower the next runtime recommendation instead of being cancelled out.")
    if _zone_plan(slc_april_follow_up_reduced, "Backyard Right").user_watering_coefficient != 0.9:
        failures.append("Reduced per-zone coefficient should still be preserved on the next-day follow-up plan.")
    if _zone_plan(slc_april_follow_up_reduced, "Backyard Right").deficit_inches >= _zone_plan(
        slc_april_follow_up,
        "Backyard Right",
    ).deficit_inches:
        failures.append("Reduced per-zone coefficient should keep the next-day grass deficit below the baseline follow-up case.")
    if _zone_runtime(slc_april_follow_up_reduced, "Backyard Right") >= _zone_runtime(
        slc_april_follow_up,
        "Backyard Right",
    ):
        failures.append("Reduced per-zone coefficient should still lower the follow-up runtime after yesterday's lighter run.")
    if _zone_runtime(slc_april_threshold, "Garden") != 8:
        failures.append("Crossing the threshold should release the banked spring garden demand without stacking extra minutes on top.")
    if _zone_plan(slc_april_threshold, "Garden").runtime_bank_minutes != 0:
        failures.append("Once the spring garden crosses the threshold, the carryover bank should clear.")
    if _zone_runtime(slc_april_xeric, "Front Yard Perennials") != 0:
        failures.append("Cool-spring drought-tolerant drip zones should still be spacing out and skipping recent runs.")
    if _zone_plan(slc_april_xeric, "Front Yard Perennials").days_until_due is None:
        failures.append("Cool-spring drought-tolerant drip zones should report when they are due again.")
    if _zone_runtime(slc_march, "Backyard Right") > 20:
        failures.append("Salt Lake March grass should stay highly restrained.")
    if _zone_runtime(slc_march_warm, "Backyard Right") < _zone_runtime(slc_march, "Backyard Right"):
        failures.append("Warm dry March startup should not water less grass than the cool-start March scenario.")
    if _zone_runtime(slc_march_warm, "Backyard Right") > 30:
        failures.append("Warm dry March startup should still stay below midsummer-style grass runtimes.")
    if _zone_runtime(slc_march_warm, "Front Yard Perennials") > 45:
        failures.append("Warm dry March startup should not push drip perennials into an aggressive midsummer runtime.")
    if _zone_runtime(slc_threshold_floor, "Garden") != 0:
        failures.append("Sub-threshold spring garden demand should stay banked instead of running immediately.")
    if _zone_plan(slc_threshold_floor, "Garden").runtime_bank_minutes != 8:
        failures.append("Carryover bank should act as a floor without double counting the next day's deficit.")
    if _zone_runtime(slc_threshold_force, "Garden") != 8:
        failures.append("A zone that has gone 7+ days without water should run even when still below the threshold.")
    if not _zone_plan(slc_threshold_force, "Garden").forced_by_skip_limit:
        failures.append("Forced below-threshold runs should be marked as coming from the skip-limit override.")
    if _zone_runtime(slc_threshold_rain, "Garden") != 0:
        failures.append("Meaningful rain should still keep the banked spring zone off for the day.")
    if _zone_plan(slc_threshold_rain, "Garden").runtime_bank_minutes != 0:
        failures.append("Meaningful rain should clear the banked sub-threshold carryover.")
    if slc_windy_hold.decision != "weather_hold":
        failures.append("Windy conditions above the configured threshold should trigger a weather hold.")
    if _zone_runtime(slc_windy_hold, "Backyard Right") != 0:
        failures.append("A weather-held windy day should not actually water the grass zone.")
    if not _zone_plan(slc_windy_hold, "Backyard Right").banked_by_weather_hold:
        failures.append("A windy weather hold should bank the skipped grass runtime.")
    if _zone_plan(slc_windy_hold, "Backyard Right").runtime_bank_minutes <= 0:
        failures.append("A windy weather hold should preserve a positive grass runtime bank.")
    if slc_gust_hold.decision != "weather_hold":
        failures.append("A gust-only storm should still trigger a weather hold when gust protection is configured.")
    if "gust" not in slc_gust_hold.reason.lower():
        failures.append("Gust-triggered weather holds should explain that the gust threshold was exceeded.")
    if slc_gust_hold.et_today_inches != slc_april.et_today_inches:
        failures.append("Wind gust should not change ET when the average wind speed is unchanged.")
    if slc_rotary_breezy.decision != "run":
        failures.append("Rotary/stream wind mode should allow a breezy shoulder-season run that spray mode would hold.")
    if _zone_plan(
        slc_rotary_breezy,
        "Backyard Right",
    ).effective_max_watering_wind_speed_mph <= slc_april.max_watering_wind_speed_mph:
        failures.append("Rotary/stream wind mode should raise the effective sustained wind threshold above the spray baseline.")
    if _zone_runtime(slc_drip_breezy, "Front Yard Perennials") <= 0:
        failures.append("Drip/bubbler zones should still be allowed to run during windy mornings.")
    if _zone_plan(slc_drip_breezy, "Front Yard Perennials").weather_hold_active:
        failures.append("Drip/bubbler zones should not be marked weather-held by wind.")
    if slc_mixed_wind_profiles.decision != "run":
        failures.append("Mixed per-zone wind profiles should still allow the controller to run when at least one zone is eligible.")
    if _zone_runtime(slc_mixed_wind_profiles, "Backyard Right") <= 0:
        failures.append("A rotary/stream zone should still run in the mixed-profile breezy scenario.")
    if _zone_runtime(slc_mixed_wind_profiles, "Front Yard Perennials") <= 0:
        failures.append("A drip/bubbler zone should still run in the mixed-profile breezy scenario.")
    if _zone_runtime(slc_mixed_wind_profiles, "Garden") != 0:
        failures.append("A standard-spray zone should still be held in the mixed-profile breezy scenario.")
    if not _zone_plan(slc_mixed_wind_profiles, "Garden").weather_hold_active:
        failures.append("The standard-spray zone should be explicitly weather-held in the mixed-profile breezy scenario.")
    if slc_rotary_gust_breezy.decision != "run":
        failures.append("Rotary/stream wind mode should allow a moderate gust-only shoulder-season run that spray mode would hold.")
    if _zone_runtime(slc_rotary_gust_breezy, "Backyard Right") <= 0:
        failures.append("A rotary/stream zone should still run when only the gust threshold separates it from standard spray.")
    if _zone_plan(slc_rotary_gust_breezy, "Backyard Right").max_watering_gust_speed_mph <= _zone_plan(
        slc_gust_hold,
        "Backyard Right",
    ).max_watering_gust_speed_mph:
        failures.append("Rotary/stream wind mode should raise the effective gust threshold above the standard spray baseline.")
    if _zone_runtime(slc_persisted_wind_stop, "Backyard Right") != 0:
        failures.append("A persisted same-day wind stop should keep the affected zone off for the rest of the day.")
    if not _zone_plan(slc_persisted_wind_stop, "Backyard Right").banked_by_weather_hold:
        failures.append("Persisted same-day wind stops should keep the affected zone banked.")
    if slc_cold_hold.decision != "weather_hold":
        failures.append("Cold conditions below the configured minimum should trigger a weather hold.")
    if _zone_runtime(slc_cold_hold, "Backyard Right") != 0:
        failures.append("A cold weather hold should not actually water the grass zone.")
    if not _zone_plan(slc_cold_hold, "Backyard Right").banked_by_weather_hold:
        failures.append("A cold weather hold should bank the skipped grass runtime.")
    if slc_cold_overrides_drip.decision != "weather_hold":
        failures.append("Global cold-weather holds should still pause the controller even when a zone uses the drip/bubbler wind profile.")
    if _zone_runtime(slc_cold_overrides_drip, "Front Yard Perennials") != 0:
        failures.append("A drip/bubbler zone should still stay off during a global cold-weather hold.")
    if not _zone_plan(slc_cold_overrides_drip, "Front Yard Perennials").banked_by_weather_hold:
        failures.append("A drip/bubbler zone should still bank runtime during a global cold-weather hold.")
    if _zone_runtime(slc_calm_after_wind_bank, "Backyard Right") != max(
        22,
        _zone_runtime(slc_april, "Backyard Right"),
    ):
        failures.append("A prior weather-held bank should act as a floor on the next calm day without double counting.")
    if _zone_runtime(slc_rain_clears_weather_bank, "Backyard Right") != 0:
        failures.append("Rain after a weather hold should keep the zone off for the day.")
    if _zone_plan(slc_rain_clears_weather_bank, "Backyard Right").runtime_bank_minutes != 0:
        failures.append("Rain after a weather hold should clear the carried runtime bank.")
    if slc_dry_windy.et_today_inches <= slc_hot.et_today_inches:
        failures.append("Dry windy air should raise the daily ET estimate above the hot baseline.")
    if slc_humid_calm.et_today_inches >= slc_hot.et_today_inches:
        failures.append("Humid calm air should suppress daily ET below the hot baseline.")
    if slc_dry_windy.raw_deficit_inches <= slc_humid_calm.raw_deficit_inches:
        failures.append("Dry windy summer conditions should create a larger raw deficit than humid calm conditions.")
    if _zone_plan(slc_sandy_garden, "Garden").effective_minimum_run_threshold_minutes >= _zone_plan(
        slc_clay_garden,
        "Garden",
    ).effective_minimum_run_threshold_minutes:
        failures.append("Clay-heavy garden soil should hold a higher minimum-run threshold than sandy soil.")
    if _zone_runtime(slc_sandy_garden, "Garden") <= _zone_runtime(slc_clay_garden, "Garden"):
        failures.append("Lower-storage sandy garden soil should release the spring garden runtime earlier than clay-heavy soil.")
    if slc_drizzle.raw_deficit_inches <= slc_light.raw_deficit_inches:
        failures.append("Sub-threshold drizzle should not suppress demand more than meaningful light rain.")
    if slc_drizzle.raw_deficit_inches < slc_soaker.raw_deficit_inches:
        failures.append("Trace drizzle should not beat a single soaker on effective rain credit.")
    if slc_light.raw_deficit_inches >= slc_soaker.raw_deficit_inches:
        failures.append("Frequent light rain should reduce more demand than a single soaker.")
    if slc_soaker.raw_deficit_inches >= slc_hot.raw_deficit_inches:
        failures.append("Single soaker scenario should still reduce demand below the full hot/dry case.")
    if slc_delay.decision != "rain_delay":
        failures.append("Recent soaking rain should create a rain-delay decision.")
    if _zone_runtime(slc_xeric, "Front Yard Perennials") != 0:
        failures.append("Water-efficient drip beds should skip when they were watered recently.")
    if _zone_plan(slc_xeric, "Front Yard Perennials").days_until_due is None:
        failures.append("Water-efficient drip spacing should report when the zone is due again.")
    if _zone_runtime(slc_xeric_short, "Front Yard Perennials") <= 0:
        failures.append("Very short manual test runs should not suppress drought-tolerant spacing.")
    if len(_zone_plan(slc_xeric_short, "Front Yard Perennials").cycle_minutes) != 1:
        failures.append("Drought-tolerant drip zones should stay as one deep single-session run.")
    vegetable_zone = _zone_plan(slc_vegetables, "Garden")
    if vegetable_zone.recommended_runtime_minutes > 20:
        failures.append("Raised-bed vegetable mode should cap hot-summer runs at about 20 minutes.")
    if vegetable_zone.target_interval_days != 1:
        failures.append("Raised-bed vegetable mode should allow near-daily summer watering.")
    if not vegetable_zone.vegetable_garden_mode:
        failures.append("Raised-bed vegetable scenario should mark the zone as being in vegetable garden mode.")
    vegetable_due_zone = _zone_plan(slc_vegetables_due, "Garden")
    if vegetable_due_zone.recommended_runtime_minutes <= 0:
        failures.append("Raised-bed vegetables should run again once the short summer interval has elapsed.")
    if vegetable_due_zone.recommended_runtime_minutes > 20:
        failures.append("Raised-bed vegetables should still keep each hot-summer run capped near 20 minutes.")
    default_profile_zone = _zone_plan(slc_profile_default, "Front Yard Perennials")
    drought_profile_zone = _zone_plan(slc_profile_drought, "Front Yard Perennials")
    trees_profile_zone = _zone_plan(slc_profile_trees, "Front Yard Perennials")
    vegetable_profile_zone = _zone_plan(slc_profile_vegetable, "Front Yard Perennials")
    if not (
        drought_profile_zone.deficit_inches
        < trees_profile_zone.deficit_inches
        < default_profile_zone.deficit_inches
        < vegetable_profile_zone.deficit_inches
    ):
        failures.append(
            "Per-zone deficits should order profile aggressiveness as drought tolerant < trees/shrubs < default lawn < vegetable garden."
        )
    if not (
        drought_profile_zone.weekly_target_inches
        < trees_profile_zone.weekly_target_inches
        < default_profile_zone.weekly_target_inches
        < vegetable_profile_zone.weekly_target_inches
    ):
        failures.append(
            "Per-zone weekly targets should order profile aggressiveness as drought tolerant < trees/shrubs < default lawn < vegetable garden."
        )
    if trees_profile_zone.target_interval_days is None or trees_profile_zone.target_interval_days < 2:
        failures.append("Trees / shrubs profile should use a multi-day target interval.")
    if len(trees_profile_zone.cycle_minutes) > 1:
        failures.append("Trees / shrubs profile should stay as one deeper single-session run.")
    trees_recent_zone = _zone_plan(slc_trees_recent, "Front Yard Perennials")
    if trees_recent_zone.recommended_runtime_minutes != 0:
        failures.append("Trees / shrubs profile should skip when it had a recent deeper run.")
    if trees_recent_zone.days_until_due is None:
        failures.append("Trees / shrubs profile should report when the zone is due again after a recent run.")
    if atl_storm.decision != "defer":
        failures.append("Atlanta stormy forecast should defer watering.")
    if hou.decision != "defer":
        failures.append("Houston stormy forecast should defer watering.")
    if mia.decision not in {"defer", "rain_delay", "skip"}:
        failures.append("Tropical wet Miami pattern should not produce a normal run decision.")
    if _zone_runtime(atl_dry, "Backyard Right") <= 0:
        failures.append("Atlanta dry streak should still water when rain is absent.")
    if _zone_runtime(stg, "Backyard Right") < _zone_runtime(sea, "Backyard Right"):
        failures.append("St. George desert heat should not water less than Seattle summer.")
    if _zone_runtime(phx, "Backyard Right") < _zone_runtime(den, "Backyard Right"):
        failures.append("Phoenix extreme heat should not water less than Denver high plains.")
    if sac.raw_deficit_inches <= sdc.raw_deficit_inches:
        failures.append("Sacramento inland heat should outpace San Diego's marine spring demand.")
    if sdc.raw_deficit_inches >= sea.raw_deficit_inches:
        failures.append("San Diego marine spring should stay below Seattle's dry-summer demand.")
    if _zone_runtime(bos, "Backyard Right") <= _zone_runtime(msp_may, "Backyard Right"):
        failures.append("Boston summer should exceed a cool Minneapolis spring runtime.")
    if bos.raw_deficit_inches >= slc_hot.raw_deficit_inches:
        failures.append("Boston summer should stay below Salt Lake interior midsummer total demand.")
    if _zone_runtime(msp_jan, "Backyard Right") > 5 or msp_jan.decision == "run":
        failures.append("Minneapolis January dormant scenario should effectively skip.")
    if msp_may.raw_deficit_inches >= sea.raw_deficit_inches:
        failures.append("Cool northern spring should remain below Seattle summer demand.")

    perennials_target = _zone_plan(slc_hot, "Front Yard Perennials").weekly_target_inches
    garden_target = _zone_plan(slc_hot, "Garden").weekly_target_inches
    grass_target = _zone_plan(slc_hot, "Backyard Right").weekly_target_inches
    if not (perennials_target < garden_target < grass_target):
        failures.append("Salt Lake summer weekly targets should order as perennials < garden < grass.")

    capped_zone = _zone_plan(slc_cap, "Backyard Right")
    if not capped_zone.capped_by_weekly_limit or capped_zone.recommended_runtime_minutes > 10:
        failures.append("Weekly cap scenario should trim grass runtime to the remaining weekly budget.")
    if capped_zone.cycle_minutes != (10,):
        failures.append("Weekly-cap-trimmed grass runtime should remain a single short cycle.")

    hot_grass = _zone_plan(slc_hot, "Backyard Right")
    hot_perennials = _zone_plan(slc_hot, "Front Yard Perennials")
    hot_garden = _zone_plan(slc_hot, "Garden")
    if not hot_grass.capped_by_session_limit or len(hot_grass.cycle_minutes) < 2:
        failures.append("Hot-summer rotary grass should hit the session cap and split into cycle-and-soak.")
    if len(hot_perennials.cycle_minutes) < 2:
        failures.append("Hot-summer drip perennials should split into multiple cycles.")
    if len(hot_garden.cycle_minutes) < 2:
        failures.append("Hot-summer bubbler garden should split into multiple soak cycles.")
    if slc_hot.available_window_minutes <= 120:
        failures.append("Hot-summer automatic windows should expand beyond the old two-hour floor.")
    if len(_zone_plan(slc_april, "Backyard Right").cycle_minutes) != 1:
        failures.append("Moderate shoulder-season grass should stay in a single cycle.")
    if (
        _zone_runtime(slc_april, "Front Yard Perennials") > 0
        and len(_zone_plan(slc_april, "Front Yard Perennials").cycle_minutes) != 1
    ):
        failures.append("Moderate shoulder-season drip runtime should stay in a single cycle.")
    if not slc_manual_window.window_rotation_applied:
        failures.append("A constrained manual window should trigger carry-forward rotation.")
    if slc_manual_window.available_window_minutes != 60:
        failures.append("Manual one-hour test window should report 60 available minutes.")
    if _zone_runtime(slc_manual_window, "Garden") <= 0 or _zone_runtime(slc_manual_window, "Backyard Right") != 0:
        failures.append("One-hour manual window should favor the due garden zone first when everything is equally stale.")
    if not _zone_plan(slc_manual_window, "Backyard Right").deferred_by_window_limit:
        failures.append("Unschedulled grass should be marked as deferred by the window limit.")
    if _zone_runtime(slc_manual_window_rotated, "Backyard Right") != 45 or _zone_runtime(slc_manual_window_rotated, "Garden") != 0:
        failures.append("After a recent garden run, the one-hour manual window should rotate the grass zone in next.")
    if not _zone_plan(slc_manual_window_rotated, "Garden").deferred_by_window_limit:
        failures.append("Garden should show window-limit deferral when the grass rotates into the short window.")
    if _zone_runtime(dallas_spring_warm, "Backyard Right") > 25:
        failures.append("Dallas April warm-season turf should stay materially restrained in spring.")
    if _zone_runtime(dallas_summer_warm, "Backyard Right") < _zone_runtime(dallas_spring_warm, "Backyard Right"):
        failures.append("Dallas warm-season turf should still run harder in midsummer than in spring.")
    if _zone_plan(dallas_fall_warm, "Backyard Right").deficit_inches >= _zone_plan(
        dallas_summer_warm, "Backyard Right"
    ).deficit_inches:
        failures.append("Dallas warm-season turf should taper back down by October.")
    if _zone_runtime(dallas_winter_warm, "Backyard Right") > 5:
        failures.append("Dallas winter warm-season turf should effectively stay dormant.")
    if dallas_spring_wet.total_recommended_runtime_minutes != 0:
        failures.append("Dallas warm-season turf with a wet spring pattern should stay off.")
    if _zone_plan(dallas_shaded_warm, "Backyard Right").deficit_inches >= _zone_plan(
        dallas_summer_warm, "Backyard Right"
    ).deficit_inches:
        failures.append("Shade plus fixed spray should reduce Dallas warm-season turf runtime below the full-sun rotary case.")
    if chicago_forecast_below_amount.decision != "run":
        failures.append("Forecast just below the rain-amount threshold should not defer.")
    if chicago_forecast_at_threshold.decision != "defer":
        failures.append("Forecast at the configured amount/probability threshold should defer when deficit is moderate.")
    if chicago_forecast_below_probability.decision != "run":
        failures.append("Forecast just below the rain-probability threshold should not defer.")
    if _zone_plan(seattle_shaded_spray, "Backyard Right").deficit_inches >= _zone_plan(
        sea, "Backyard Right"
    ).deficit_inches:
        failures.append("Seattle shaded fixed-spray grass should run below the default Seattle full-sun rotary case.")
    if _zone_runtime(slc_threshold_cap, "Garden") != 0 or _zone_plan(slc_threshold_cap, "Garden").runtime_bank_minutes != 8:
        failures.append("A remaining weekly cap below the minimum threshold should keep the spring garden banked instead of forcing a partial run.")
    if slc_wind_at_threshold.decision != "weather_hold":
        failures.append("Wind exactly at the configured threshold should still trigger a weather hold.")
    if slc_wind_below_threshold.decision != "run":
        failures.append("Wind just below the configured threshold should not trigger a weather hold.")
    if _zone_runtime(slc_wind_below_threshold, "Backyard Right") <= 0:
        failures.append("Wind just below the configured threshold should still allow grass runtime on a dry shoulder-season day.")
    if slc_temp_at_threshold.decision != "weather_hold":
        failures.append("Temperature exactly at the configured minimum should still trigger a weather hold.")
    if slc_temp_above_threshold.decision == "weather_hold":
        failures.append("Temperature just above the configured minimum should allow normal planning instead of a weather hold.")
    if slc_combined_weather_hold.decision != "weather_hold":
        failures.append("Combined cold and windy conditions should still produce a weather-hold decision.")
    if "wind speed" not in slc_combined_weather_hold.reason or "temperature" not in slc_combined_weather_hold.reason:
        failures.append("Combined cold/wind weather holds should explain both active hold reasons.")
    if _zone_plan(slc_windy_bank_no_stack, "Backyard Right").runtime_bank_minutes != _zone_runtime(slc_hot, "Backyard Right"):
        failures.append("Repeated windy holds should keep the bank at the current required runtime instead of stacking a new full runtime on top.")
    if _zone_plan(slc_windy_cap_hold, "Backyard Right").runtime_bank_minutes > 30:
        failures.append("Weather-held runtime banks should still respect the remaining weekly cap.")
    if _zone_runtime(slc_calm_bank_weekly_cap, "Backyard Right") > 15:
        failures.append("Released weather banks should not exceed the remaining weekly cap on the next calm day.")
    if _zone_runtime(slc_calm_bank_weekly_cap, "Backyard Right") != 15:
        failures.append("Released weather banks should use the remaining weekly cap cleanly when it is still above zero.")
    if slc_rain_delay_over_weather.decision != "rain_delay":
        failures.append("Rain delay should outrank simultaneous cold/windy weather holds.")
    if _zone_plan(slc_rain_delay_over_weather, "Backyard Right").runtime_bank_minutes != 0:
        failures.append("Rain delay should clear any previously banked weather-held runtime.")
    if slc_no_application_rates.decision != "not_configured":
        failures.append("Controllers with no measured application rates should stay in a not-configured monitoring state.")
    if any(zone.application_rate_configured for zone in slc_no_application_rates.zone_plans):
        failures.append("Zones without measured application rates should stay marked as not configured.")
    if not all(
        "Application rate is not configured" in zone.reason
        for zone in slc_no_application_rates.zone_plans
    ):
        failures.append("Unset zones should explain that they need calibration or a measured application rate.")
    if slc_partial_application_rates.decision != "run":
        failures.append("Controllers should still plan configured zones when another enabled zone has not been calibrated yet.")
    if _zone_runtime(slc_partial_application_rates, "Backyard Right") <= 0:
        failures.append("Configured zones should still receive a runtime recommendation when neighboring zones are unconfigured.")
    if _zone_runtime(slc_partial_application_rates, "Front Yard Perennials") != 0:
        failures.append("Unconfigured zones should not receive a runtime recommendation.")
    if "Application rate is not configured" not in _zone_plan(
        slc_partial_application_rates,
        "Front Yard Perennials",
    ).reason:
        failures.append("Partially configured controllers should surface the unconfigured-zone reason on the affected zone.")

    for location in _matrix_locations():
        prefix = f"Matrix {location.name}"
        spring_dry = results[f"{prefix} spring dry"]
        spring_wet = results[f"{prefix} spring wet"]
        summer_dry = results[f"{prefix} summer dry"]
        spring_cold_snap = results[f"{prefix} spring cold snap hold"]
        summer_breezy = results[f"{prefix} summer breezy below hold"]
        summer_windy_hold = results[f"{prefix} summer windy hold"]
        summer_trace = results[f"{prefix} summer trace drizzle"]
        summer_light = results[f"{prefix} summer light daily rain"]
        summer_soaker = results[f"{prefix} summer single soaker"]
        summer_forecast = results[f"{prefix} summer forecast storm"]
        fall_dry = results[f"{prefix} fall dry"]
        drought_recent = results[f"{prefix} summer drought tolerant recent"]
        trees_compare = results[f"{prefix} summer trees compare"]
        trees_recent = results[f"{prefix} summer trees recent"]
        vegetable_due = results[f"{prefix} summer vegetable due"]

        if _zone_runtime(summer_dry, "Backyard Right") < _zone_runtime(spring_dry, "Backyard Right"):
            failures.append(f"{location.name} summer dry grass should not water less than spring dry.")
        if _zone_runtime(summer_dry, "Front Yard Perennials") < _zone_runtime(spring_dry, "Front Yard Perennials"):
            failures.append(f"{location.name} summer dry perennials should not water less than spring dry.")
        if _zone_runtime(summer_dry, "Garden") < _zone_runtime(spring_dry, "Garden"):
            failures.append(f"{location.name} summer dry garden should not water less than spring dry.")
        if _zone_runtime(fall_dry, "Backyard Right") > _zone_runtime(summer_dry, "Backyard Right"):
            failures.append(f"{location.name} fall dry grass should stay below summer dry demand.")
        if summer_trace.total_recommended_runtime_minutes < summer_soaker.total_recommended_runtime_minutes:
            failures.append(f"{location.name} trace drizzle should not suppress total demand more than a single soaker.")
        if summer_light.raw_deficit_inches >= summer_soaker.raw_deficit_inches:
            failures.append(f"{location.name} frequent light rain should reduce the raw deficit more than a single soaker.")
        if _zone_runtime(summer_dry, "Backyard Right") < _zone_runtime(summer_trace, "Backyard Right"):
            failures.append(f"{location.name} dry baseline should not water less grass than trace-drizzle conditions.")
        if summer_forecast.total_recommended_runtime_minutes > summer_dry.total_recommended_runtime_minutes:
            failures.append(f"{location.name} forecast-storm scenario should not recommend more watering than summer dry.")
        if spring_wet.decision not in {"skip", "rain_delay", "defer"}:
            failures.append(f"{location.name} wet spring should not remain on a normal run decision.")
        if spring_wet.total_recommended_runtime_minutes != 0:
            failures.append(f"{location.name} wet spring should keep all zones off for the day.")
        if spring_cold_snap.decision != "weather_hold":
            failures.append(f"{location.name} spring cold snap should trigger a weather hold.")
        if _zone_runtime(spring_cold_snap, "Backyard Right") != 0:
            failures.append(f"{location.name} spring cold snap should not run the grass zone.")
        if _zone_plan(spring_cold_snap, "Backyard Right").runtime_bank_minutes <= 0:
            failures.append(f"{location.name} spring cold snap should bank a positive grass runtime.")
        if summer_breezy.decision != "run":
            failures.append(f"{location.name} breezy summer scenario below the hold threshold should remain a normal run.")
        if _zone_runtime(summer_breezy, "Backyard Right") <= 0:
            failures.append(f"{location.name} breezy summer scenario below the hold threshold should still water grass.")
        if summer_windy_hold.decision not in {"run", "weather_hold"}:
            failures.append(f"{location.name} high-wind summer scenario should stay in a weather-driven decision state.")
        if _zone_runtime(summer_windy_hold, "Garden") != 0:
            failures.append(f"{location.name} high-wind summer scenario should hold the standard-spray garden zone.")
        if not _zone_plan(summer_windy_hold, "Garden").weather_hold_active:
            failures.append(f"{location.name} high-wind summer scenario should explicitly weather-hold the standard-spray garden zone.")
        if _zone_runtime(summer_windy_hold, "Backyard Right") != 0:
            failures.append(f"{location.name} high-wind summer scenario should hold the rotary grass zone once wind exceeds the higher rotary threshold.")
        if not _zone_plan(summer_windy_hold, "Backyard Right").weather_hold_active:
            failures.append(f"{location.name} high-wind summer scenario should explicitly weather-hold the rotary grass zone.")
        if _zone_plan(summer_windy_hold, "Garden").runtime_bank_minutes <= 0:
            failures.append(f"{location.name} high-wind summer scenario should bank a positive standard-spray runtime.")
        if summer_windy_hold.decision == "run" and _zone_runtime(summer_windy_hold, "Front Yard Perennials") <= 0:
            failures.append(f"{location.name} high-wind summer scenario should only keep the controller running when the drip zone still has runtime to apply.")
        if _zone_runtime(drought_recent, "Front Yard Perennials") != 0:
            failures.append(f"{location.name} drought-tolerant recent perennials should still be spacing out.")
        if _zone_plan(drought_recent, "Front Yard Perennials").days_until_due is None:
            failures.append(f"{location.name} drought-tolerant recent perennials should report when they are due again.")
        if _zone_plan(drought_recent, "Front Yard Perennials").cycle_minutes not in {(), (0,), (90,)}:
            failures.append(f"{location.name} drought-tolerant profile should not split into soak cycles.")
        trees_compare_zone = _zone_plan(trees_compare, "Front Yard Perennials")
        summer_default_zone = _zone_plan(summer_dry, "Front Yard Perennials")
        if trees_compare_zone.deficit_inches >= summer_default_zone.deficit_inches:
            failures.append(f"{location.name} trees/shrubs profile should accumulate less deficit than the lawn default under matching dry conditions.")
        if trees_compare_zone.target_interval_days is None or trees_compare_zone.target_interval_days < 2:
            failures.append(f"{location.name} trees/shrubs profile should use a multi-day watering interval.")
        if len(trees_compare_zone.cycle_minutes) > 1:
            failures.append(f"{location.name} trees/shrubs profile should stay as one deeper single-session run.")
        trees_recent_zone = _zone_plan(trees_recent, "Front Yard Perennials")
        if trees_recent_zone.recommended_runtime_minutes != 0:
            failures.append(f"{location.name} trees/shrubs recent profile should skip after a recent deeper run.")
        if trees_recent_zone.days_until_due is None:
            failures.append(f"{location.name} trees/shrubs recent profile should report when it is due again.")
        vegetable_zone = _zone_plan(vegetable_due, "Garden")
        if vegetable_zone.recommended_runtime_minutes <= 0:
            failures.append(f"{location.name} summer vegetable profile should become due after a two-day gap.")
        if vegetable_zone.recommended_runtime_minutes > 20:
            failures.append(f"{location.name} summer vegetable profile should cap each session near 20 minutes.")
        if not vegetable_zone.vegetable_garden_mode:
            failures.append(f"{location.name} vegetable matrix scenario should mark the zone as a vegetable garden.")
        if summer_dry.available_window_minutes != summer_dry.total_requested_runtime_minutes:
            failures.append(
                f"{location.name} summer dry automatic window should size directly to the requested runtime."
            )
        if summer_dry.suggested_end_time > "09:00":
            failures.append(
                f"{location.name} summer dry automatic window should still finish in the early morning."
            )

    return failures


def _validate_bucket_matrix_invariants(results: dict[str, object]) -> list[str]:
    """Ensure broad bucket-model invariants hold across the scenario matrix."""

    failures: list[str] = []
    for name, plan in results.items():
        if float(plan.deficit_inches) < -0.001:
            failures.append(f"{name} should never expose a negative controller deficit.")
        if float(plan.raw_deficit_inches) < -0.001:
            failures.append(f"{name} should never expose a negative raw controller deficit.")
        for zone_plan in plan.zone_plans:
            if float(zone_plan.deficit_inches) < -0.001:
                failures.append(
                    f"{name} / {zone_plan.zone_name} should never expose a negative zone deficit."
                )
            if float(zone_plan.current_water_inches) < -0.001:
                failures.append(
                    f"{name} / {zone_plan.zone_name} should never expose a negative current-water bucket."
                )
            if float(zone_plan.current_water_inches) > float(zone_plan.capacity_inches) + 0.001:
                failures.append(
                    f"{name} / {zone_plan.zone_name} current water should stay clamped to capacity."
                )
            if not zone_plan.application_rate_configured and zone_plan.recommended_runtime_minutes != 0:
                failures.append(
                    f"{name} / {zone_plan.zone_name} should not recommend runtime when application rate is unset."
                )
            derived_deficit = round(
                float(zone_plan.capacity_inches) - float(zone_plan.current_water_inches),
                3,
            )
            if abs(float(zone_plan.deficit_inches) - max(0.0, derived_deficit)) > 0.002:
                failures.append(
                    f"{name} / {zone_plan.zone_name} deficit should be derived from capacity - current water."
                )

    no_rates = results["Salt Lake April no application rates configured"]
    if no_rates.decision != "not_configured":
        failures.append(
            "Controllers with no measured application rates should stay in a not-configured monitoring state."
        )

    partial_rates = results["Salt Lake April partial application rates configured"]
    if partial_rates.decision == "not_configured":
        failures.append(
            "Controllers with at least one calibrated/configured zone should not stay in the not-configured state."
        )
    unconfigured_zone = _zone_plan(partial_rates, "Front Yard Perennials")
    if unconfigured_zone.application_rate_configured:
        failures.append("The partial-configuration scenario should still leave Front Yard Perennials unconfigured.")
    if unconfigured_zone.recommended_runtime_minutes != 0:
        failures.append("Unconfigured zones should not receive a runtime recommendation.")
    if "Application rate is not configured" not in unconfigured_zone.reason:
        failures.append("Unconfigured zones should explain that they need calibration or an application rate.")

    return failures


def _build_bucket_test_plan(
    models,
    planner,
    *,
    now_local: datetime,
    current_water_inches: float,
    window_start: dt_time,
    window_end: dt_time,
    temperature_f: float = 72.0,
    uv_index: float = 6.0,
    humidity_percent: float = 35.0,
    wind_speed_mph: float = 4.0,
    wind_gust_mph: float | None = None,
    hourly_et_inches: float = 0.02,
    application_rate_inches_per_hour: float = 0.60,
    profile: str = "Default (lawn)",
    sprinkler_head_type: str = "Standard spray",
    trigger_buffer_inches: float = 0.05,
    controller_day_restrictions: dict[str, str] | None = None,
    zone_root_depths: dict[int, float] | None = None,
    zone_soil_whc: dict[int, float] | None = None,
    zone_mad_values: dict[int, float] | None = None,
    zone_kc_values: dict[int, float] | None = None,
) -> object:
    """Build a focused single-zone bucket plan for trigger and hold validation."""

    scenario = ScenarioSpec(
        name="Bucket validation",
        now_local=now_local,
        latitude=40.76,
        longitude=-111.89,
        temperature_f=temperature_f,
        uv_index=uv_index,
        humidity_percent=humidity_percent,
        wind_speed_mph=wind_speed_mph,
        wind_gust_mph=wind_gust_mph,
        rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        use_automatic_window=False,
        manual_window_start=window_start,
        manual_window_end=window_end,
    )
    controller = _build_controller(models, scenario)
    records = _build_records(models, planner, scenario)

    zone_watering_profiles = {
        "ctrl1:1": profile,
        "ctrl1:2": "Disabled",
        "ctrl1:3": "Disabled",
    }
    zone_application_rates = {"ctrl1:1": application_rate_inches_per_hour}
    zone_sprinkler_wind_profiles = {"ctrl1:1": sprinkler_head_type}
    zone_trigger_buffer_map = {"ctrl1:1": trigger_buffer_inches}
    zone_root_depth_map = {
        f"ctrl1:{zone_number}": value
        for zone_number, value in (zone_root_depths or {}).items()
    }
    zone_soil_whc_map = {
        f"ctrl1:{zone_number}": value
        for zone_number, value in (zone_soil_whc or {}).items()
    }
    zone_mad_map = {
        f"ctrl1:{zone_number}": value
        for zone_number, value in (zone_mad_values or {}).items()
    }
    zone_kc_map = {
        f"ctrl1:{zone_number}": value
        for zone_number, value in (zone_kc_values or {}).items()
    }

    bucket_states = {}
    last_hour_key = (
        now_local.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    ).strftime("%Y-%m-%dT%H")
    for zone in controller.zones:
        agronomy = planner.resolve_zone_agronomy(
            device_id="ctrl1",
            zone_number=zone.zone_number,
            zone_watering_profiles=zone_watering_profiles,
            zone_root_depths=zone_root_depth_map,
            zone_soil_whc=zone_soil_whc_map,
            zone_mad_values=zone_mad_map,
            zone_kc_values=zone_kc_map,
            zone_trigger_buffers=zone_trigger_buffer_map,
        )
        capacity_inches = float(agronomy["capacity_in"])
        zone_current_water = capacity_inches
        if zone.zone_number == 1:
            zone_current_water = current_water_inches
        bucket_states[f"ctrl1:{zone.zone_number}"] = models.BhyveZoneBucketState(
            capacity_inches=round(capacity_inches, 3),
            current_water_inches=round(zone_current_water, 3),
            last_bucket_update=now_local.isoformat(),
            last_et_hour_key=last_hour_key,
            last_authoritative_et_date=None,
            last_effective_rain_date=now_local.date().isoformat(),
            last_effective_rain_total_inches=0.0,
            last_irrigation_event_key=None,
        )

    return planner.build_controller_plan(
        controller=controller,
        now_local=now_local,
        daily_records=records,
        daily_rain_inches=0.0,
        rain_active_hours_24h=None,
        latitude=40.76,
        longitude=-111.89,
        location_source="scenario_runner",
        temperature_f=temperature_f,
        uv_index=uv_index,
        irradiance_w_m2=None,
        humidity_percent=humidity_percent,
        wind_speed_mph=wind_speed_mph,
        wind_gust_mph=wind_gust_mph,
        forecast_rain_amount_inches=None,
        forecast_rain_probability=None,
        overall_watering_coefficient=1.0,
        minimum_run_threshold_minutes=10,
        max_watering_wind_speed_mph=12.0,
        min_watering_temperature_f=40.0,
        zone_application_rates=zone_application_rates,
        max_weekly_runtime_minutes={},
        zone_watering_coefficients={},
        zone_watering_profiles=zone_watering_profiles,
        zone_sprinkler_wind_profiles=zone_sprinkler_wind_profiles,
        controller_watering_day_restrictions={
            f"ctrl1:{weekday_key}": mode
            for weekday_key, mode in (controller_day_restrictions or {}).items()
        },
        zone_watering_day_restrictions={},
        zone_runtime_banks={},
        start_time_by_device={"ctrl1:start": window_start},
        end_time_by_device={"ctrl1:end": window_end},
        automatic_window_enabled_by_device={"ctrl1": False},
        automatic_window_preference_by_device={"ctrl1": "Morning (dawn)"},
        automatic_window_max_minutes_by_device={"ctrl1": 480},
        zone_weather_stop_holds={},
        zone_bucket_states=bucket_states,
        zone_root_depths=zone_root_depth_map,
        zone_soil_whc=zone_soil_whc_map,
        zone_mad_values=zone_mad_map,
        zone_kc_values=zone_kc_map,
        zone_trigger_buffers=zone_trigger_buffer_map,
        hourly_et_inches=hourly_et_inches,
        et_source="test_hourly_et",
    )


def _validate_bucket_trigger_projection(models, planner) -> list[str]:
    """Validate daylight-only trigger projection and overnight fallback rules."""

    failures: list[str] = []
    daytime_now = _dt_for_zone(7, 10, 10, 0, TZ_DENVER)
    daytime_window_start = dt_time(12, 0)
    daytime_window_end = dt_time(13, 0)

    base_plan = _build_bucket_test_plan(
        models,
        planner,
        now_local=daytime_now,
        current_water_inches=1.05,
        window_start=daytime_window_start,
        window_end=daytime_window_end,
    )
    base_zone = _zone_plan(base_plan, "Backyard Right")
    if base_zone.projected_daylight_hours <= 0:
        failures.append("Daytime trigger projection should count daylight hours before the next allowed window.")
        return failures

    buffer_inches = float(base_zone.trigger_buffer_inches)
    projected_draw_inches = float(base_zone.projected_et_draw_inches)

    exact_plan = _build_bucket_test_plan(
        models,
        planner,
        now_local=daytime_now,
        current_water_inches=projected_draw_inches + buffer_inches,
        window_start=daytime_window_start,
        window_end=daytime_window_end,
    )
    if not _zone_plan(exact_plan, "Backyard Right").trigger_active:
        failures.append("Trigger projection should fire when projected remaining water is exactly at the buffer.")

    below_plan = _build_bucket_test_plan(
        models,
        planner,
        now_local=daytime_now,
        current_water_inches=max(0.0, projected_draw_inches + buffer_inches - 0.01),
        window_start=daytime_window_start,
        window_end=daytime_window_end,
    )
    if not _zone_plan(below_plan, "Backyard Right").trigger_active:
        failures.append("Trigger projection should fire when projected remaining water falls below the buffer.")

    above_plan = _build_bucket_test_plan(
        models,
        planner,
        now_local=daytime_now,
        current_water_inches=projected_draw_inches + buffer_inches + 0.01,
        window_start=daytime_window_start,
        window_end=daytime_window_end,
    )
    if _zone_plan(above_plan, "Backyard Right").trigger_active:
        failures.append("Trigger projection should stay off when projected remaining water stays above the buffer.")

    full_plan = _build_bucket_test_plan(
        models,
        planner,
        now_local=daytime_now,
        current_water_inches=float(base_zone.capacity_inches),
        window_start=daytime_window_start,
        window_end=daytime_window_end,
    )
    if _zone_plan(full_plan, "Backyard Right").trigger_active:
        failures.append("A zone already at capacity should not trigger a watering run.")

    empty_plan = _build_bucket_test_plan(
        models,
        planner,
        now_local=daytime_now,
        current_water_inches=0.0,
        window_start=daytime_window_start,
        window_end=daytime_window_end,
    )
    if not _zone_plan(empty_plan, "Backyard Right").trigger_active:
        failures.append("A zone with zero current water should always trigger a watering run.")

    overnight_now = _dt_for_zone(7, 10, 23, 0, TZ_DENVER)
    overnight_window_start = dt_time(4, 0)
    overnight_window_end = dt_time(7, 0)
    overnight_base = _build_bucket_test_plan(
        models,
        planner,
        now_local=overnight_now,
        current_water_inches=float(base_zone.capacity_inches),
        window_start=overnight_window_start,
        window_end=overnight_window_end,
    )
    overnight_zone = _zone_plan(overnight_base, "Backyard Right")
    if overnight_zone.projected_daylight_hours != 0:
        failures.append("Overnight trigger projection should not count daylight ET before a pre-dawn watering window.")

    overnight_capacity = float(overnight_zone.capacity_inches)
    overnight_buffer = float(overnight_zone.trigger_buffer_inches)
    overnight_exact = _build_bucket_test_plan(
        models,
        planner,
        now_local=overnight_now,
        current_water_inches=overnight_capacity - overnight_buffer,
        window_start=overnight_window_start,
        window_end=overnight_window_end,
    )
    if not _zone_plan(overnight_exact, "Backyard Right").trigger_active:
        failures.append("When projected daylight hours are zero, the imminent-window fallback should trigger exactly at the deficit buffer.")

    overnight_above = _build_bucket_test_plan(
        models,
        planner,
        now_local=overnight_now,
        current_water_inches=overnight_capacity - max(0.0, overnight_buffer - 0.01),
        window_start=overnight_window_start,
        window_end=overnight_window_end,
    )
    if _zone_plan(overnight_above, "Backyard Right").trigger_active:
        failures.append("When projected daylight hours are zero, the imminent-window fallback should stay off above the deficit buffer.")

    return failures


def _validate_disabled_profile_holds_full_bucket(models, planner) -> list[str]:
    """Ensure disabled zones keep a full bucket instead of banking hidden debt."""

    failures: list[str] = []
    now_local = _dt_for_zone(7, 10, 12, 0, TZ_DENVER)
    plan = _build_bucket_test_plan(
        models,
        planner,
        now_local=now_local,
        current_water_inches=0.0,
        window_start=dt_time(4, 0),
        window_end=dt_time(7, 0),
        hourly_et_inches=0.04,
        profile="Disabled",
    )
    zone = _zone_plan(plan, "Backyard Right")
    if zone.deficit_inches != 0:
        failures.append("Disabled profile zones should display zero deficit even if the stored bucket was empty.")
    if zone.current_water_inches != zone.capacity_inches:
        failures.append("Disabled profile zones should hold current water at capacity so re-enabling starts fresh.")
    if zone.trigger_active or zone.recommended_runtime_minutes != 0:
        failures.append("Disabled profile zones should not trigger or recommend runtime while held at full bucket.")
    if zone.zone_hourly_et_inches != 0 or zone.zone_daily_et_inches != 0:
        failures.append("Disabled profile zones should not accrue ET while excluded from planning.")

    return failures


def _validate_next_window_computation(models, planner) -> list[str]:
    """Ensure next-window computation respects before/inside/after and disabled days."""

    failures: list[str] = []
    scenario = ScenarioSpec(
        name="Window validation",
        now_local=_dt_for_zone(7, 10, 3, 0, TZ_DENVER),
        latitude=40.76,
        longitude=-111.89,
        temperature_f=65.0,
        uv_index=5.0,
        rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    )
    controller = _build_controller(models, scenario)
    zone_profiles = {
        "ctrl1:1": "Default (lawn)",
        "ctrl1:2": "Disabled",
        "ctrl1:3": "Disabled",
    }
    before_now = _dt_for_zone(7, 10, 3, 0, TZ_DENVER)
    inside_now = _dt_for_zone(7, 10, 5, 0, TZ_DENVER)
    after_now = _dt_for_zone(7, 10, 8, 0, TZ_DENVER)

    before_start = planner.compute_next_window_start(
        now_local=before_now,
        controller=controller,
        allowed_start_time=dt_time(4, 0),
        allowed_end_time=dt_time(7, 0),
        zone_watering_profiles=zone_profiles,
        controller_day_restrictions={},
        zone_day_restrictions={},
    )
    if before_start.date() != before_now.date() or before_start.time() != dt_time(4, 0):
        failures.append("Before the active window, the next watering window should still be today's configured start time.")

    inside_start = planner.compute_next_window_start(
        now_local=inside_now,
        controller=controller,
        allowed_start_time=dt_time(4, 0),
        allowed_end_time=dt_time(7, 0),
        zone_watering_profiles=zone_profiles,
        controller_day_restrictions={},
        zone_day_restrictions={},
    )
    if inside_start.date() != inside_now.date() + timedelta(days=1) or inside_start.time() != dt_time(4, 0):
        failures.append("Inside the active window, the next watering window should move to tomorrow's start time.")

    after_start = planner.compute_next_window_start(
        now_local=after_now,
        controller=controller,
        allowed_start_time=dt_time(4, 0),
        allowed_end_time=dt_time(7, 0),
        zone_watering_profiles=zone_profiles,
        controller_day_restrictions={},
        zone_day_restrictions={},
    )
    if after_start.date() != after_now.date() + timedelta(days=1) or after_start.time() != dt_time(4, 0):
        failures.append("After the active window, the next watering window should move to tomorrow's start time.")

    disabled_today_start = planner.compute_next_window_start(
        now_local=before_now,
        controller=controller,
        allowed_start_time=dt_time(4, 0),
        allowed_end_time=dt_time(7, 0),
        zone_watering_profiles=zone_profiles,
        controller_day_restrictions={f"ctrl1:{planner._weekday_key(before_now.date())}": "Disabled"},
        zone_day_restrictions={},
    )
    if disabled_today_start.date() <= before_now.date():
        failures.append("Disabled watering days should push the next watering window to a later allowed day.")

    return failures


def _validate_profile_defaults_and_capacity(planner) -> list[str]:
    """Validate explicit agronomy defaults and derived capacities."""

    failures: list[str] = []
    expected_defaults = {
        "Default (lawn)": (14.0, 0.15, 0.50, 1.0),
        "Drought tolerant": (20.0, 0.15, 0.40, 0.6),
        "Vegetable garden": (8.0, 0.20, 0.50, 1.0),
        "Trees / shrubs": (30.0, 0.15, 0.45, 0.7),
        "Annual flowers / containers": (6.0, 0.20, 0.55, 0.8),
        "Native / xeriscape": (24.0, 0.12, 0.35, 0.4),
    }

    for zone_number, (profile, values) in enumerate(expected_defaults.items(), start=1):
        root_depth_inches, soil_whc, mad, kc = values
        agronomy = planner.resolve_zone_agronomy(
            device_id="ctrl1",
            zone_number=zone_number,
            zone_watering_profiles={f"ctrl1:{zone_number}": profile},
            zone_root_depths={},
            zone_soil_whc={},
            zone_mad_values={},
            zone_kc_values={},
            zone_trigger_buffers={},
        )
        expected_capacity = round(root_depth_inches * soil_whc * mad, 3)
        if (
            agronomy["root_depth_in"] != round(root_depth_inches, 2)
            or agronomy["soil_whc_in_per_in"] != round(soil_whc, 3)
            or agronomy["mad"] != round(mad, 3)
            or agronomy["kc"] != round(kc, 3)
            or agronomy["capacity_in"] != expected_capacity
        ):
            failures.append(
                f"{profile} should seed explicit agronomy defaults and capacity {expected_capacity:.3f} in."
            )

    override_agronomy = planner.resolve_zone_agronomy(
        device_id="ctrl1",
        zone_number=99,
        zone_watering_profiles={"ctrl1:99": "Default (lawn)"},
        zone_root_depths={"ctrl1:99": 10.0},
        zone_soil_whc={"ctrl1:99": 0.20},
        zone_mad_values={"ctrl1:99": 0.60},
        zone_kc_values={"ctrl1:99": 0.90},
        zone_trigger_buffers={"ctrl1:99": 0.08},
    )
    if override_agronomy["capacity_in"] != 1.2:
        failures.append("Capacity should update live from root depth * WHC * MAD overrides.")
    if override_agronomy["trigger_buffer_in"] != 0.08:
        failures.append("Trigger buffer overrides should be preserved per zone.")

    return failures


def _validate_capacity_migration(planner, models) -> list[str]:
    """Validate ratio-preserving bucket migration when capacity changes."""

    failures: list[str] = []
    half_full = models.BhyveZoneBucketState(
        capacity_inches=1.05,
        current_water_inches=0.525,
        last_bucket_update=None,
        last_et_hour_key=None,
        last_authoritative_et_date=None,
        last_effective_rain_date=None,
        last_effective_rain_total_inches=0.0,
        last_irrigation_event_key=None,
    )
    migrated_half_full, half_meta = planner.migrate_bucket_capacity(
        half_full,
        capacity_inches=2.10,
    )
    if migrated_half_full.current_water_inches != 1.05 or float(half_meta["fill_ratio"]) != 0.5:
        failures.append("Capacity migration should preserve the bucket fill ratio instead of the absolute water depth.")

    full_bucket = models.BhyveZoneBucketState(
        capacity_inches=1.05,
        current_water_inches=1.05,
        last_bucket_update=None,
        last_et_hour_key=None,
        last_authoritative_et_date=None,
        last_effective_rain_date=None,
        last_effective_rain_total_inches=0.0,
        last_irrigation_event_key=None,
    )
    migrated_full, full_meta = planner.migrate_bucket_capacity(
        full_bucket,
        capacity_inches=0.60,
    )
    if migrated_full.current_water_inches != 0.60:
        failures.append("When capacity shrinks, the new bucket should preserve ratio and end up full when the old bucket was full.")
    if not bool(full_meta["clamped_to_full"]):
        failures.append("Shrinking a full bucket below its old absolute water level should report that the zone was set to full.")

    return failures


def _validate_weather_holds_under_bucket(models, planner) -> list[str]:
    """Ensure wind/cold behavior still works when trigger logic is bucket-based."""

    failures: list[str] = []
    trigger_now = _dt_for_zone(7, 10, 10, 0, TZ_DENVER)
    windy_spray = _build_bucket_test_plan(
        models,
        planner,
        now_local=trigger_now,
        current_water_inches=0.0,
        window_start=dt_time(12, 0),
        window_end=dt_time(13, 0),
        wind_speed_mph=20.0,
        sprinkler_head_type="Standard spray",
    )
    if windy_spray.decision != "weather_hold":
        failures.append("Triggered standard-spray zones should still enter a weather hold when wind exceeds the configured threshold.")
    if _zone_plan(windy_spray, "Backyard Right").recommended_runtime_minutes != 0:
        failures.append("Weather-held zones should not actually schedule runtime under the bucket trigger model.")

    windy_drip = _build_bucket_test_plan(
        models,
        planner,
        now_local=trigger_now,
        current_water_inches=0.0,
        window_start=dt_time(12, 0),
        window_end=dt_time(14, 0),
        wind_speed_mph=20.0,
        sprinkler_head_type="Drip / bubbler",
    )
    if _zone_plan(windy_drip, "Backyard Right").weather_hold_active:
        failures.append("Drip / bubbler zones should not be wind-held when the bucket says they need water.")
    if _zone_plan(windy_drip, "Backyard Right").recommended_runtime_minutes <= 0:
        failures.append("Drip / bubbler zones should still run under windy conditions when the bucket is empty.")

    cold_drip = _build_bucket_test_plan(
        models,
        planner,
        now_local=trigger_now,
        current_water_inches=0.0,
        window_start=dt_time(12, 0),
        window_end=dt_time(13, 0),
        temperature_f=35.0,
        sprinkler_head_type="Drip / bubbler",
    )
    if cold_drip.decision != "weather_hold":
        failures.append("Cold-weather holds should still pause watering even for drip / bubbler zones.")

    return failures


def _validate_latest_event_fallback(models, planner) -> list[str]:
    """Ensure latest_event still counts if recent_events is empty."""

    now_local = _dt_for_zone(7, 20, 6, 0, TZ_DENVER)
    end_dt = now_local - timedelta(days=1)
    latest_event = models.BhyveLatestEvent(
        duration=20 * 60,
        end_local=end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        end_ts=int(end_dt.timestamp()),
        schedule_name="Bhyve App Quick Run",
        schedule_type="MANUAL",
    )
    zone = models.BhyveSprinklerZone(
        "ctrl1",
        "z1",
        1,
        "Fallback Zone",
        True,
        500.0,
        "PERENNIALS",
        0.55,
        0.0,
        10.0,
        0.0,
        0.15,
        80.0,
        "LOTS_OF_SUN",
        "CLAY",
        "FLAT",
        "DRIP_LINE",
        0.5,
        80.0,
        None,
        True,
        2400,
        1200,
        "sched1",
        0.0,
        None,
        (),
        (),
        latest_event,
        (),
        (),
    )
    controller = models.BhyveSprinklerControllerSnapshot(
        "ctrl1",
        "Sprinklers",
        "BS_WK1",
        "Common",
        "Common",
        True,
        (zone,),
        None,
    )
    since_utc = now_local.astimezone(timezone.utc) - timedelta(days=7)
    minutes, inches = planner.calc_recent_zone_irrigation(zone, since_utc, 0.40)
    runs = planner._collect_recent_controller_runs(controller, since_utc)

    failures: list[str] = []
    if minutes != 20:
        failures.append("latest_event fallback should count Bhyve app watering even when recent_events is empty.")
    if inches <= 0:
        failures.append("latest_event fallback should still estimate applied irrigation inches.")
    if len(runs) != 1 or runs[0].schedule_type != "MANUAL":
        failures.append("Controller recent-run history should include latest_event-only manual Bhyve runs.")
    return failures


def _validate_effective_rain_curve(planner) -> list[str]:
    """Ensure the effective-rain function stays smooth and monotonic."""

    failures: list[str] = []
    sample_points = [round(index * 0.01, 2) for index in range(0, 151)]
    values = [planner.calc_effective_rain(point) for point in sample_points]

    for index in range(1, len(values)):
        if values[index] + 1e-9 < values[index - 1]:
            failures.append(
                f"Effective-rain curve should be monotonic, but {sample_points[index - 1]:.2f} in -> "
                f"{sample_points[index]:.2f} in decreased from {values[index - 1]:.3f} to {values[index]:.3f}."
            )
            break

    trace_credit = planner.calc_effective_rain(0.09)
    if trace_credit <= 0.0:
        failures.append("Trace rainfall below 0.10 in should still receive some effective-rain credit.")
    if trace_credit > 0.09:
        failures.append("Trace rainfall credit should not exceed the raw rainfall amount.")

    if abs(planner.calc_effective_rain(0.50) - planner.calc_effective_rain(0.51)) > 0.02:
        failures.append("Effective-rain output should no longer show a visible hard jump at 0.50 in.")

    if abs(planner.calc_effective_rain(0.25) - planner.calc_effective_rain(0.26)) > 0.02:
        failures.append("Effective-rain output should no longer show a visible hard jump at 0.25 in.")

    if planner.calc_effective_rain(1.50) > 0.60:
        failures.append("Effective rain should remain capped at 0.60 in for very large rain totals.")

    if planner.calc_effective_rain(0.50) < planner.calc_effective_rain(0.25):
        failures.append("A half-inch rain should credit more effective rain than a quarter-inch rain.")

    return failures


def _validate_effective_rain_timing(planner) -> list[str]:
    """Ensure slower soaking rain credits more than a short burst of the same total."""

    failures: list[str] = []

    quarter_inch_burst = planner.calc_effective_rain(0.25, 1.0)
    quarter_inch_soak = planner.calc_effective_rain(0.25, 12.0)
    if quarter_inch_soak <= quarter_inch_burst:
        failures.append(
            "A quarter-inch spread across many hours should credit more effective rain than a 1-hour burst."
        )

    half_inch_burst = planner.calc_effective_rain(0.50, 1.0)
    half_inch_soak = planner.calc_effective_rain(0.50, 8.0)
    if half_inch_soak <= half_inch_burst:
        failures.append(
            "A half-inch soaking rain should credit more effective rain than the same total in a short burst."
        )

    if planner.calc_effective_rain(0.25, 12.0) - planner.calc_effective_rain(0.25, 1.0) < 0.03:
        failures.append(
            "Rain timing should have a visible effect; the planner should distinguish a 12-hour soak from a 1-hour burst."
        )

    if planner.calc_effective_rain(0.25, 4.0) < planner.calc_effective_rain(0.25, 1.0):
        failures.append("Longer-spread rainfall should never be less effective than a shorter burst of the same total.")

    return failures


def _validate_automatic_window_preferences(planner) -> list[str]:
    """Ensure automatic windows anchor to dawn or sunset and size to runtime."""

    failures: list[str] = []
    window_anchor = _dt_for_zone(4, 20, 12, 0, TZ_DENVER)
    window_date = window_anchor.date()
    common_kwargs = {
        "zones": (),
        "for_date": window_date,
        "latitude": 40.76,
        "longitude": -111.89,
        "utc_offset_hours": _utc_offset_hours(window_anchor),
        "temperature_f": 63.0,
        "maximum_window_minutes": 480,
    }

    morning_start, morning_end, _ = planner.suggest_watering_window(
        total_runtime_minutes=240,
        timing_preference="Morning (dawn)",
        **common_kwargs,
    )
    evening_start, evening_end, _ = planner.suggest_watering_window(
        total_runtime_minutes=240,
        timing_preference="Evening (sunset)",
        **common_kwargs,
    )
    morning_duration = planner._window_duration_minutes(morning_start, morning_end)
    evening_duration = planner._window_duration_minutes(evening_start, evening_end)

    if morning_duration != 240:
        failures.append(
            f"Morning automatic window should preserve a 240-minute runtime, got {morning_duration} minutes."
        )
    if evening_duration != 240:
        failures.append(
            f"Evening automatic window should preserve a 240-minute runtime, got {evening_duration} minutes."
        )
    if morning_end >= dt_time(9, 0):
        failures.append(
            f"Morning automatic window should finish near dawn, not {morning_end.strftime('%H:%M')}."
        )
    if evening_start <= dt_time(17, 0):
        failures.append(
            f"Evening automatic window should start near sunset, not {evening_start.strftime('%H:%M')}."
        )
    if morning_end >= evening_start:
        failures.append(
            "Morning automatic window should finish earlier in the day than the evening automatic window starts."
        )

    capped_start, capped_end, _ = planner.suggest_watering_window(
        total_runtime_minutes=600,
        timing_preference="Morning (dawn)",
        maximum_window_minutes=480,
        **{
            key: value
            for key, value in common_kwargs.items()
            if key != "maximum_window_minutes"
        },
    )
    capped_duration = planner._window_duration_minutes(capped_start, capped_end)
    if capped_duration != 480:
        failures.append(
            f"Automatic window should honor the configured 480-minute cap, got {capped_duration} minutes."
        )

    return failures


def _validate_accumulated_daily_et(planner) -> list[str]:
    """Ensure accumulated ET rises through the day and does not unwind at night."""

    failures: list[str] = []
    latitude = 40.76
    longitude = -111.89
    common_kwargs = {
        "latitude": latitude,
        "longitude": longitude,
        "temperature_f": 82.0,
        "uv_index": 8.0,
        "humidity_percent": 25.0,
        "wind_speed_mph": 6.0,
    }

    morning_dt = _dt_for_zone(7, 10, 7, 0, TZ_DENVER)
    noon_dt = _dt_for_zone(7, 10, 13, 0, TZ_DENVER)
    sunset_dt = _dt_for_zone(7, 10, 21, 30, TZ_DENVER)
    late_night_dt = _dt_for_zone(7, 10, 23, 0, TZ_DENVER)

    morning_et, _, _, morning_progress = planner.calc_accumulated_daily_et_inches(
        morning_dt,
        **common_kwargs,
    )
    noon_et, _, _, noon_progress = planner.calc_accumulated_daily_et_inches(
        noon_dt,
        **common_kwargs,
    )
    sunset_et, _, _, sunset_progress = planner.calc_accumulated_daily_et_inches(
        sunset_dt,
        **common_kwargs,
    )
    late_night_et, _, _, late_night_progress = planner.calc_accumulated_daily_et_inches(
        late_night_dt,
        **common_kwargs,
    )

    if not (0.0 <= morning_progress < noon_progress <= sunset_progress <= 1.0):
        failures.append(
            "Accumulated ET progress should move forward through the daylight period."
        )
    if morning_et >= noon_et:
        failures.append(
            f"Accumulated ET should increase from morning to noon, got {morning_et} then {noon_et}."
        )
    if noon_et >= sunset_et:
        failures.append(
            f"Accumulated ET should increase from noon to sunset, got {noon_et} then {sunset_et}."
        )
    if late_night_progress != 1.0:
        failures.append(
            f"Accumulated ET should be fully accrued after sunset, got progress {late_night_progress}."
        )
    if late_night_et != sunset_et:
        failures.append(
            f"Accumulated ET should flatten after sunset, got {sunset_et} at sunset and {late_night_et} later at night."
        )

    return failures


def _validate_zone_deficit_not_reweighted_by_live_weather(models, planner) -> list[str]:
    """Ensure live weather cannot reduce an existing zone deficit without water input."""

    failures: list[str] = []
    now_local = _dt_for_zone(4, 10, 14, 0, TZ_DENVER)
    scenario = ScenarioSpec(
        name="Validation deficit stability",
        now_local=now_local,
        latitude=40.76,
        longitude=-111.89,
        temperature_f=72.0,
        uv_index=6.0,
        humidity_percent=35.0,
        wind_speed_mph=4.0,
        rain_pattern_inches=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    )
    controller = _build_controller(models, scenario)
    records = tuple(
        models.BhyveDailyWaterBalance(
            (now_local.date() - timedelta(days=6 - index)).isoformat(),
            0.0,
            0.0,
            0.08,
        )
        for index in range(7)
    )
    zone_application_rate_map = {
        f"ctrl1:{zone_number}": rate
        for zone_number, rate in _default_zone_application_rates().items()
    }
    zone_sprinkler_wind_profiles = {
        f"ctrl1:{zone_number}": profile
        for zone_number, profile in _default_zone_sprinkler_head_types().items()
    }

    def _plan_for_weather(
        *,
        temperature_f: float,
        humidity_percent: float,
        wind_speed_mph: float,
    ):
        return planner.build_controller_plan(
            controller=controller,
            now_local=now_local,
            daily_records=records,
            daily_rain_inches=0.0,
            rain_active_hours_24h=None,
            latitude=40.76,
            longitude=-111.89,
            location_source="scenario_runner",
            temperature_f=temperature_f,
            uv_index=6.0,
            irradiance_w_m2=None,
            humidity_percent=humidity_percent,
            wind_speed_mph=wind_speed_mph,
            wind_gust_mph=None,
            forecast_rain_amount_inches=None,
            forecast_rain_probability=None,
            overall_watering_coefficient=1.0,
            minimum_run_threshold_minutes=10,
            max_watering_wind_speed_mph=12.0,
            min_watering_temperature_f=40.0,
            zone_application_rates=zone_application_rate_map,
            max_weekly_runtime_minutes={},
            zone_watering_coefficients={},
            zone_watering_profiles={},
            zone_sprinkler_wind_profiles=zone_sprinkler_wind_profiles,
            controller_watering_day_restrictions={},
            zone_watering_day_restrictions={},
            zone_runtime_banks={},
            start_time_by_device={},
            end_time_by_device={},
            automatic_window_enabled_by_device={},
            automatic_window_preference_by_device={},
            automatic_window_max_minutes_by_device={"ctrl1": 480},
            et_today_override_inches=0.04,
            zone_weather_stop_holds={},
        )

    warm_plan = _plan_for_weather(
        temperature_f=78.0,
        humidity_percent=20.0,
        wind_speed_mph=6.0,
    )
    cool_plan = _plan_for_weather(
        temperature_f=42.0,
        humidity_percent=80.0,
        wind_speed_mph=1.0,
    )

    warm_grass_deficit = _zone_plan(warm_plan, "Backyard Right").deficit_inches
    cool_grass_deficit = _zone_plan(cool_plan, "Backyard Right").deficit_inches
    if warm_grass_deficit != cool_grass_deficit:
        failures.append(
            "Live temperature, humidity, or wind should not retroactively reweight a zone's existing deficit when rain, irrigation, and accrued ET are unchanged."
        )

    return failures


def main() -> None:
    """Print representative current-planner outputs and validate them."""

    models, planner = _load_live_modules()
    results = {
        scenario.name: _run_scenario(models, planner, scenario)
        for scenario in _scenario_specs()
    }

    print("Planner scenario matrix")
    print("=======================")
    for scenario in _scenario_specs():
        plan = results[scenario.name]
        print()
        print(
            f"{scenario.name}: decision={plan.decision}, deficit={plan.deficit_inches:.3f} in, "
            f"window={plan.suggested_start_time}-{plan.suggested_end_time}"
        )
        if scenario.note:
            print(f"  note: {scenario.note}")
        for zone_plan in plan.zone_plans:
            print(
                "  "
                f"{zone_plan.zone_name}: runtime={zone_plan.recommended_runtime_minutes} min, "
                f"seasonal_factor={zone_plan.seasonal_factor:.3f}, "
                f"session_cap={zone_plan.session_limit_minutes} min, "
                f"weekly_target={zone_plan.weekly_target_inches:.3f} in"
            )

    failures: list[str] = []
    failures.extend(_validate_bucket_matrix_invariants(results))
    failures.extend(_validate_bucket_trigger_projection(models, planner))
    failures.extend(_validate_disabled_profile_holds_full_bucket(models, planner))
    failures.extend(_validate_next_window_computation(models, planner))
    failures.extend(_validate_profile_defaults_and_capacity(planner))
    failures.extend(_validate_capacity_migration(planner, models))
    failures.extend(_validate_weather_holds_under_bucket(models, planner))
    failures.extend(_validate_latest_event_fallback(models, planner))
    failures.extend(_validate_effective_rain_curve(planner))
    failures.extend(_validate_effective_rain_timing(planner))
    failures.extend(_validate_automatic_window_preferences(planner))
    failures.extend(_validate_accumulated_daily_et(planner))
    failures.extend(_validate_zone_deficit_not_reweighted_by_live_weather(models, planner))
    print()
    print("Validation summary")
    print("==================")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        raise SystemExit(1)

    print(f"PASS: {len(results)} scenarios and all planner expectations passed.")


if __name__ == "__main__":
    main()
