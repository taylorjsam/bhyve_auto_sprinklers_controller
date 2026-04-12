"""Serialize and restore planner-facing runtime configuration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import time as dt_time
import logging
from typing import TYPE_CHECKING, Any

from .const import (
    DEFAULT_AUTOMATIC_WATERING_ENABLED,
    DEFAULT_AUTOMATIC_WINDOW_PREFERENCE,
    DEFAULT_MAX_AUTOMATIC_WATERING_WINDOW_MINUTES,
    DEFAULT_MAX_WATERING_WIND_SPEED_MPH,
    DEFAULT_MINIMUM_RUN_THRESHOLD_MINUTES,
    DEFAULT_MIN_WATERING_TEMPERATURE_F,
    DEFAULT_NOTIFICATIONS_ENABLED,
    DEFAULT_OVERALL_WATERING_COEFFICIENT,
    MAX_AUTOMATIC_WATERING_WINDOW_MINUTES,
    MAX_MINIMUM_RUN_THRESHOLD_MINUTES,
    MAX_WATERING_WIND_SPEED_MPH,
    MAX_WATERING_TEMPERATURE_F,
    MAX_WEEKLY_RUN_TIME,
    MAX_ZONE_APPLICATION_RATE_IN_PER_HOUR,
    MAX_ZONE_KC,
    MAX_ZONE_MAD,
    MAX_ZONE_ROOT_DEPTH_IN,
    MAX_ZONE_SOIL_WHC_IN_PER_IN,
    MAX_ZONE_TRIGGER_BUFFER_INCHES,
    MAX_ZONE_WATERING_COEFFICIENT,
    MIN_AUTOMATIC_WATERING_WINDOW_MINUTES,
    MIN_WATERING_TEMPERATURE_F,
    MIN_ZONE_KC,
    MIN_ZONE_MAD,
    MIN_ZONE_ROOT_DEPTH_IN,
    MIN_ZONE_SOIL_WHC_IN_PER_IN,
    MIN_ZONE_TRIGGER_BUFFER_INCHES,
    MIN_ZONE_WATERING_COEFFICIENT,
    normalize_automatic_window_preference,
    normalize_day_restriction,
    normalize_zone_watering_profile,
    DEFAULT_ZONE_SPRINKLER_WIND_PROFILE,
    SPRINKLER_WIND_PROFILE_DRIP_BUBBLER,
    SPRINKLER_WIND_PROFILE_ROTARY_STREAM,
    SPRINKLER_WIND_PROFILE_STANDARD_SPRAY,
)

if TYPE_CHECKING:
    from .models import BhyveRuntimeData


_LOGGER = logging.getLogger(__name__)


def serialize_runtime_config_snapshot(runtime_data: "BhyveRuntimeData") -> dict[str, Any]:
    """Return the planner-facing runtime config as JSON-safe data."""

    return {
        "zone_application_rates": _serialize_float_dict(runtime_data.zone_application_rates, 3),
        "zone_root_depths": _serialize_float_dict(runtime_data.zone_root_depths, 3),
        "zone_soil_whc": _serialize_float_dict(runtime_data.zone_soil_whc, 4),
        "zone_mad_values": _serialize_float_dict(runtime_data.zone_mad_values, 4),
        "zone_kc_values": _serialize_float_dict(runtime_data.zone_kc_values, 4),
        "zone_trigger_buffers": _serialize_float_dict(runtime_data.zone_trigger_buffers, 4),
        "max_weekly_run_times": _serialize_int_dict(runtime_data.max_weekly_run_times),
        "zone_watering_coefficients": _serialize_float_dict(
            runtime_data.zone_watering_coefficients,
            4,
        ),
        "zone_watering_profiles": {
            key: normalize_zone_watering_profile(value)
            for key, value in runtime_data.zone_watering_profiles.items()
            if value is not None
        },
        "zone_sprinkler_wind_profiles": {
            key: _normalize_sprinkler_head_type(value)
            for key, value in runtime_data.zone_sprinkler_wind_profiles.items()
            if value is not None
        },
        "controller_watering_day_restrictions": {
            key: normalize_day_restriction(value)
            for key, value in runtime_data.controller_watering_day_restrictions.items()
            if value is not None
        },
        "watering_window_times": {
            key: value.isoformat(timespec="minutes")
            for key, value in runtime_data.watering_window_times.items()
            if isinstance(value, dt_time)
        },
        "automatic_window_preferences": {
            key: normalize_automatic_window_preference(value)
            for key, value in runtime_data.automatic_window_preferences.items()
            if value is not None
        },
        "automatic_window_max_minutes": _serialize_int_dict(
            runtime_data.automatic_window_max_minutes
        ),
        "automatic_window_enabled": {
            key: bool(value) for key, value in runtime_data.automatic_window_enabled.items()
        },
        "overall_watering_coefficient": round(
            float(runtime_data.overall_watering_coefficient),
            4,
        ),
        "minimum_run_threshold_minutes": int(runtime_data.minimum_run_threshold_minutes),
        "max_watering_wind_speed_mph": round(
            float(runtime_data.max_watering_wind_speed_mph),
            3,
        ),
        "min_watering_temperature_f": round(
            float(runtime_data.min_watering_temperature_f),
            3,
        ),
        "automatic_watering_enabled": bool(runtime_data.automatic_watering_enabled),
        "notifications_enabled": bool(runtime_data.notifications_enabled),
        "notification_service": runtime_data.notification_service,
    }


def deserialize_runtime_config_snapshot(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize a stored runtime-config snapshot."""

    if not isinstance(snapshot, Mapping):
        snapshot = {}

    return {
        "zone_application_rates": _deserialize_bounded_float_dict(
            snapshot.get("zone_application_rates"),
            label="zone application rate",
            minimum=0.0,
            maximum=MAX_ZONE_APPLICATION_RATE_IN_PER_HOUR,
        ),
        "zone_root_depths": _deserialize_bounded_float_dict(
            snapshot.get("zone_root_depths"),
            label="zone root depth",
            minimum=MIN_ZONE_ROOT_DEPTH_IN,
            maximum=MAX_ZONE_ROOT_DEPTH_IN,
        ),
        "zone_soil_whc": _deserialize_bounded_float_dict(
            snapshot.get("zone_soil_whc"),
            label="zone soil WHC",
            minimum=MIN_ZONE_SOIL_WHC_IN_PER_IN,
            maximum=MAX_ZONE_SOIL_WHC_IN_PER_IN,
        ),
        "zone_mad_values": _deserialize_bounded_float_dict(
            snapshot.get("zone_mad_values"),
            label="zone MAD",
            minimum=MIN_ZONE_MAD,
            maximum=MAX_ZONE_MAD,
        ),
        "zone_kc_values": _deserialize_bounded_float_dict(
            snapshot.get("zone_kc_values"),
            label="zone kc",
            minimum=MIN_ZONE_KC,
            maximum=MAX_ZONE_KC,
        ),
        "zone_trigger_buffers": _deserialize_bounded_float_dict(
            snapshot.get("zone_trigger_buffers"),
            label="zone trigger buffer",
            minimum=MIN_ZONE_TRIGGER_BUFFER_INCHES,
            maximum=MAX_ZONE_TRIGGER_BUFFER_INCHES,
        ),
        "max_weekly_run_times": _deserialize_bounded_int_dict(
            snapshot.get("max_weekly_run_times"),
            label="zone weekly cap",
            minimum=0,
            maximum=MAX_WEEKLY_RUN_TIME,
        ),
        "zone_watering_coefficients": _deserialize_bounded_float_dict(
            snapshot.get("zone_watering_coefficients"),
            label="zone watering coefficient",
            minimum=MIN_ZONE_WATERING_COEFFICIENT,
            maximum=MAX_ZONE_WATERING_COEFFICIENT,
        ),
        "zone_watering_profiles": {
            key: normalize_zone_watering_profile(value)
            for key, value in _deserialize_str_dict(snapshot.get("zone_watering_profiles")).items()
        },
        "zone_sprinkler_wind_profiles": {
            key: _normalize_sprinkler_head_type(value)
            for key, value in _deserialize_str_dict(
                snapshot.get("zone_sprinkler_wind_profiles")
            ).items()
        },
        "controller_watering_day_restrictions": {
            key: normalize_day_restriction(value)
            for key, value in _deserialize_str_dict(
                snapshot.get("controller_watering_day_restrictions")
            ).items()
        },
        "watering_window_times": _deserialize_time_dict(snapshot.get("watering_window_times")),
        "automatic_window_preferences": {
            key: normalize_automatic_window_preference(value)
            for key, value in _deserialize_str_dict(
                snapshot.get("automatic_window_preferences")
            ).items()
        },
        "automatic_window_max_minutes": _deserialize_bounded_int_dict(
            snapshot.get("automatic_window_max_minutes"),
            label="automatic window max minutes",
            minimum=MIN_AUTOMATIC_WATERING_WINDOW_MINUTES,
            maximum=MAX_AUTOMATIC_WATERING_WINDOW_MINUTES,
        ),
        "automatic_window_enabled": _deserialize_bool_dict(
            snapshot.get("automatic_window_enabled")
        ),
        "overall_watering_coefficient": _deserialize_bounded_float_value(
            snapshot.get("overall_watering_coefficient"),
            DEFAULT_OVERALL_WATERING_COEFFICIENT,
            label="overall watering coefficient",
            minimum=MIN_ZONE_WATERING_COEFFICIENT,
            maximum=MAX_ZONE_WATERING_COEFFICIENT,
        ),
        "minimum_run_threshold_minutes": _deserialize_bounded_int_value(
            snapshot.get("minimum_run_threshold_minutes"),
            DEFAULT_MINIMUM_RUN_THRESHOLD_MINUTES,
            label="minimum run threshold",
            minimum=0,
            maximum=MAX_MINIMUM_RUN_THRESHOLD_MINUTES,
        ),
        "max_watering_wind_speed_mph": _deserialize_bounded_float_value(
            snapshot.get("max_watering_wind_speed_mph"),
            DEFAULT_MAX_WATERING_WIND_SPEED_MPH,
            label="max watering wind speed",
            minimum=0.0,
            maximum=MAX_WATERING_WIND_SPEED_MPH,
        ),
        "min_watering_temperature_f": _deserialize_bounded_float_value(
            snapshot.get("min_watering_temperature_f"),
            DEFAULT_MIN_WATERING_TEMPERATURE_F,
            label="minimum watering temperature",
            minimum=MIN_WATERING_TEMPERATURE_F,
            maximum=MAX_WATERING_TEMPERATURE_F,
        ),
        "automatic_watering_enabled": _deserialize_bool_value(
            snapshot.get("automatic_watering_enabled"),
            DEFAULT_AUTOMATIC_WATERING_ENABLED,
        ),
        "notifications_enabled": _deserialize_bool_value(
            snapshot.get("notifications_enabled"),
            DEFAULT_NOTIFICATIONS_ENABLED,
        ),
        "notification_service": _deserialize_optional_str(snapshot.get("notification_service")),
    }


def _serialize_float_dict(values: Mapping[str, Any], digits: int) -> dict[str, float]:
    """Return a float-only dict with rounded values."""

    serialized: dict[str, float] = {}
    for key, value in values.items():
        try:
            serialized[str(key)] = round(float(value), digits)
        except (TypeError, ValueError):
            continue
    return serialized


def _serialize_int_dict(values: Mapping[str, Any]) -> dict[str, int]:
    """Return an int-only dict."""

    serialized: dict[str, int] = {}
    for key, value in values.items():
        try:
            serialized[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return serialized


def _deserialize_float_dict(values: Any) -> dict[str, float]:
    """Return a normalized float mapping."""

    if not isinstance(values, Mapping):
        return {}
    restored: dict[str, float] = {}
    for key, value in values.items():
        try:
            restored[str(key)] = float(value)
        except (TypeError, ValueError):
            _LOGGER.warning("Ignoring invalid restored float value for %s: %r", key, value)
            continue
    return restored


def _deserialize_bounded_float_dict(
    values: Any,
    *,
    label: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> dict[str, float]:
    """Return a normalized float mapping with clamped bounds."""

    restored = _deserialize_float_dict(values)
    validated: dict[str, float] = {}
    for key, value in restored.items():
        validated[str(key)] = _clamp_numeric_value(
            value,
            label=label,
            key=str(key),
            minimum=minimum,
            maximum=maximum,
        )
    return validated


def _deserialize_int_dict(values: Any) -> dict[str, int]:
    """Return a normalized int mapping."""

    if not isinstance(values, Mapping):
        return {}
    restored: dict[str, int] = {}
    for key, value in values.items():
        try:
            restored[str(key)] = int(value)
        except (TypeError, ValueError):
            _LOGGER.warning("Ignoring invalid restored integer value for %s: %r", key, value)
            continue
    return restored


def _deserialize_bounded_int_dict(
    values: Any,
    *,
    label: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> dict[str, int]:
    """Return a normalized int mapping with clamped bounds."""

    restored = _deserialize_int_dict(values)
    validated: dict[str, int] = {}
    for key, value in restored.items():
        clamped = _clamp_numeric_value(
            float(value),
            label=label,
            key=str(key),
            minimum=float(minimum) if minimum is not None else None,
            maximum=float(maximum) if maximum is not None else None,
        )
        validated[str(key)] = int(round(clamped))
    return validated


def _deserialize_str_dict(values: Any) -> dict[str, str]:
    """Return a normalized string mapping."""

    if not isinstance(values, Mapping):
        return {}
    restored: dict[str, str] = {}
    for key, value in values.items():
        if value is None:
            continue
        restored[str(key)] = str(value)
    return restored


def _deserialize_bool_dict(values: Any) -> dict[str, bool]:
    """Return a normalized bool mapping."""

    if not isinstance(values, Mapping):
        return {}
    return {str(key): bool(value) for key, value in values.items()}


def _deserialize_time_dict(values: Any) -> dict[str, dt_time]:
    """Return a normalized time mapping."""

    if not isinstance(values, Mapping):
        return {}
    restored: dict[str, dt_time] = {}
    for key, value in values.items():
        if value is None:
            continue
        try:
            restored[str(key)] = dt_time.fromisoformat(str(value))
        except (TypeError, ValueError):
            continue
    return restored


def _deserialize_float_value(value: Any, default: float) -> float:
    """Return a float or the provided default."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _deserialize_bounded_float_value(
    value: Any,
    default: float,
    *,
    label: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    """Return a bounded float or the provided default."""

    parsed = _deserialize_float_value(value, default)
    return _clamp_numeric_value(
        parsed,
        label=label,
        minimum=minimum,
        maximum=maximum,
    )


def _deserialize_int_value(value: Any, default: int) -> int:
    """Return an int or the provided default."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _deserialize_bounded_int_value(
    value: Any,
    default: int,
    *,
    label: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Return a bounded int or the provided default."""

    parsed = _deserialize_int_value(value, default)
    clamped = _clamp_numeric_value(
        float(parsed),
        label=label,
        minimum=float(minimum) if minimum is not None else None,
        maximum=float(maximum) if maximum is not None else None,
    )
    return int(round(clamped))


def _deserialize_bool_value(value: Any, default: bool) -> bool:
    """Return a bool or the provided default."""

    if value is None:
        return bool(default)
    return bool(value)


def _deserialize_optional_str(value: Any) -> str | None:
    """Return a string value or None."""

    if value in {None, ""}:
        return None
    return str(value)


def _normalize_sprinkler_head_type(value: Any) -> str:
    """Return a supported sprinkler head type."""

    normalized = str(value or "").strip()
    if normalized in {
        SPRINKLER_WIND_PROFILE_STANDARD_SPRAY,
        SPRINKLER_WIND_PROFILE_ROTARY_STREAM,
        SPRINKLER_WIND_PROFILE_DRIP_BUBBLER,
    }:
        return normalized
    return DEFAULT_ZONE_SPRINKLER_WIND_PROFILE


def _clamp_numeric_value(
    value: float,
    *,
    label: str,
    key: str | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    """Clamp a numeric restore value into a safe range and log when adjusted."""

    clamped = value
    if minimum is not None:
        clamped = max(minimum, clamped)
    if maximum is not None:
        clamped = min(maximum, clamped)
    if clamped != value:
        target = f"{label} ({key})" if key is not None else label
        _LOGGER.warning(
            "Clamped restored %s from %s to %s to keep planner runtime config in range",
            target,
            value,
            clamped,
        )
    return clamped
