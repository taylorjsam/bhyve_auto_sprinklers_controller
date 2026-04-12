"""Persistent rolling water-balance storage."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, WATER_BALANCE_WINDOW_DAYS

_STORAGE_VERSION = 1


class BhyveWaterBalanceStore:
    """Persist daily weather inputs used by the irrigation planner."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the storage helper."""

        self._store = Store[dict[str, Any]](
            hass,
            _STORAGE_VERSION,
            f"{DOMAIN}_{entry_id}_water_balance",
        )
        self._data: dict[str, Any] = {"controllers": {}}
        self._loaded = False

    async def async_load(self) -> None:
        """Load stored balance data once."""

        if self._loaded:
            return

        data = await self._store.async_load()
        if isinstance(data, dict):
            self._data = data
            self._data.setdefault("controllers", {})

        self._loaded = True

    def get_daily_records(self, device_id: str) -> dict[str, dict[str, float]]:
        """Return stored daily records for a controller."""

        controllers = self._data.get("controllers", {})
        controller = controllers.get(device_id, {})
        records = controller.get("daily_records", {})
        if not isinstance(records, Mapping):
            return {}
        return deepcopy(records)

    def get_zone_runtime_banks(self, device_id: str) -> dict[str, dict[str, float | str]]:
        """Return stored per-zone runtime carryover state for a controller."""

        controllers = self._data.get("controllers", {})
        controller = controllers.get(device_id, {})
        records = controller.get("zone_runtime_banks", {})
        if not isinstance(records, Mapping):
            return {}
        return deepcopy(records)

    def get_zone_bucket_states(self, device_id: str) -> dict[str, dict[str, Any]]:
        """Return persisted per-zone bucket state for a controller."""

        controllers = self._data.get("controllers", {})
        controller = controllers.get(device_id, {})
        records = controller.get("zone_bucket_states", {})
        if not isinstance(records, Mapping):
            return {}
        return deepcopy(records)

    def get_controller_weather_stop_hold(self, device_id: str) -> dict[str, Any]:
        """Return a persisted same-day weather-stop hold for a controller."""

        controllers = self._data.get("controllers", {})
        controller = controllers.get(device_id, {})
        hold = controller.get("weather_stop_hold", {})
        if not isinstance(hold, Mapping):
            return {}
        return deepcopy(hold)

    def get_daily_rain_tracker(self) -> dict[str, Any]:
        """Return the persisted intraday rain-timing tracker."""

        tracker = self._data.get("daily_rain_tracker", {})
        if not isinstance(tracker, Mapping):
            return {}
        return deepcopy(tracker)

    def get_daily_weather_tracker(self) -> dict[str, Any]:
        """Return the persisted intraday weather tracker."""

        tracker = self._data.get("daily_weather_tracker", {})
        if not isinstance(tracker, Mapping):
            return {}
        return deepcopy(tracker)

    def get_runtime_config_snapshot(self) -> dict[str, Any]:
        """Return persisted planner-facing runtime configuration."""

        snapshot = self._data.get("runtime_config_snapshot", {})
        if not isinstance(snapshot, Mapping):
            return {}
        return deepcopy(snapshot)

    def get_zone_weather_stop_holds(self, device_id: str) -> dict[str, dict[str, Any]]:
        """Return persisted same-day weather-stop holds keyed by zone number."""

        controllers = self._data.get("controllers", {})
        controller = controllers.get(device_id, {})
        holds = controller.get("zone_weather_stop_holds", {})
        if not isinstance(holds, Mapping):
            return {}
        return deepcopy(holds)

    async def async_upsert_daily_record(
        self,
        device_id: str,
        date_key: str,
        *,
        raw_rain_inches: float,
        effective_rain_inches: float,
        et_inches: float,
    ) -> None:
        """Insert or update a controller's daily weather record."""

        await self.async_load()
        controller = self._ensure_controller(device_id)
        records = controller.setdefault("daily_records", {})

        new_record = {
            "raw_rain_inches": round(float(raw_rain_inches), 3),
            "effective_rain_inches": round(float(effective_rain_inches), 3),
            "et_inches": round(float(et_inches), 3),
        }
        existing = records.get(date_key)
        if existing == new_record:
            return

        records[date_key] = new_record
        self._prune_records(records)
        await self._store.async_save(self._data)

    async def async_observe_daily_rain(
        self,
        *,
        date_key: str,
        raw_rain_inches: float,
        observed_at_iso: str,
    ) -> dict[str, Any]:
        """Persist an observation of the cumulative daily rain source.

        The planner only receives a cumulative "rain today" source. Track how long
        that source actually spent increasing so the effective-rain model can treat
        a quick burst differently than a slow soak across many hours.
        """

        await self.async_load()
        observed_raw = round(max(0.0, float(raw_rain_inches)), 3)
        tracker = self._data.get("daily_rain_tracker")
        if not isinstance(tracker, dict):
            tracker = {}

        new_tracker = deepcopy(tracker)
        if str(new_tracker.get("date") or "") != date_key:
            new_tracker = {
                "date": date_key,
                "last_observed_raw_inches": observed_raw,
                "last_observed_at": observed_at_iso,
                "rain_active_seconds": 0.0,
            }
        else:
            previous_raw = float(new_tracker.get("last_observed_raw_inches", 0.0) or 0.0)
            previous_observed_at = new_tracker.get("last_observed_at")
            rain_active_seconds = float(new_tracker.get("rain_active_seconds", 0.0) or 0.0)

            if isinstance(previous_observed_at, str):
                try:
                    previous_dt = datetime.fromisoformat(previous_observed_at)
                    current_dt = datetime.fromisoformat(observed_at_iso)
                except ValueError:
                    previous_dt = None
                    current_dt = None
                if (
                    previous_dt is not None
                    and current_dt is not None
                    and observed_raw > previous_raw + 0.001
                ):
                    elapsed_seconds = max(0.0, (current_dt - previous_dt).total_seconds())
                    rain_active_seconds += elapsed_seconds

            new_tracker.update(
                {
                    "date": date_key,
                    "last_observed_raw_inches": observed_raw,
                    "last_observed_at": observed_at_iso,
                    "rain_active_seconds": round(rain_active_seconds, 3),
                }
            )

        if self._data.get("daily_rain_tracker") != new_tracker:
            self._data["daily_rain_tracker"] = new_tracker
            await self._store.async_save(self._data)
        return deepcopy(new_tracker)

    async def async_observe_daily_weather(
        self,
        *,
        date_key: str,
        observed_at_iso: str,
        temperature_f: float | None,
        humidity_percent: float | None,
        wind_speed_mph: float | None,
        solar_radiation_wh_m2: float | None,
    ) -> dict[str, Any]:
        """Persist intraday weather observations used by sunset ET calculation."""

        await self.async_load()
        tracker = self._data.get("daily_weather_tracker")
        if not isinstance(tracker, dict):
            tracker = {}

        new_tracker = deepcopy(tracker)
        if str(new_tracker.get("date") or "") != date_key:
            new_tracker = {
                "date": date_key,
                "observed_count": 0,
                "solar_valid_count": 0,
                "wind_valid_count": 0,
                "wind_speed_sum_mph": 0.0,
                "temperature_min_f": None,
                "temperature_max_f": None,
                "humidity_min_percent": None,
                "humidity_max_percent": None,
                "last_temperature_f": None,
                "last_humidity_percent": None,
                "last_wind_speed_mph": None,
                "last_solar_radiation_wh_m2": None,
                "last_good_intraday_et_inches": None,
                "last_good_intraday_et_basis": None,
                "authoritative_et_inches": None,
                "authoritative_et_source": None,
                "authoritative_et_date": None,
                "authoritative_et_calculated_at": None,
            }

        new_tracker["date"] = date_key
        new_tracker["last_observed_at"] = observed_at_iso
        new_tracker["observed_count"] = int(new_tracker.get("observed_count", 0) or 0) + 1

        if temperature_f is not None:
            temp = float(temperature_f)
            current_min = new_tracker.get("temperature_min_f")
            current_max = new_tracker.get("temperature_max_f")
            new_tracker["temperature_min_f"] = (
                temp if current_min is None else min(float(current_min), temp)
            )
            new_tracker["temperature_max_f"] = (
                temp if current_max is None else max(float(current_max), temp)
            )
            new_tracker["last_temperature_f"] = temp

        if humidity_percent is not None:
            humidity = float(humidity_percent)
            current_min = new_tracker.get("humidity_min_percent")
            current_max = new_tracker.get("humidity_max_percent")
            new_tracker["humidity_min_percent"] = (
                humidity if current_min is None else min(float(current_min), humidity)
            )
            new_tracker["humidity_max_percent"] = (
                humidity if current_max is None else max(float(current_max), humidity)
            )
            new_tracker["last_humidity_percent"] = humidity

        if wind_speed_mph is not None:
            wind = float(wind_speed_mph)
            new_tracker["wind_valid_count"] = int(
                new_tracker.get("wind_valid_count", 0) or 0
            ) + 1
            new_tracker["wind_speed_sum_mph"] = round(
                float(new_tracker.get("wind_speed_sum_mph", 0.0) or 0.0) + wind,
                4,
            )
            new_tracker["last_wind_speed_mph"] = wind

        if solar_radiation_wh_m2 is not None:
            solar = max(0.0, float(solar_radiation_wh_m2))
            new_tracker["solar_valid_count"] = int(
                new_tracker.get("solar_valid_count", 0) or 0
            ) + 1
            previous_solar = float(new_tracker.get("last_solar_radiation_wh_m2", 0.0) or 0.0)
            new_tracker["last_solar_radiation_wh_m2"] = round(
                max(previous_solar, solar),
                4,
            )

        if self._data.get("daily_weather_tracker") != new_tracker:
            self._data["daily_weather_tracker"] = new_tracker
            await self._store.async_save(self._data)
        return deepcopy(new_tracker)

    async def async_update_daily_weather_tracker(
        self,
        updates: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Merge additional fields into the daily weather tracker."""

        await self.async_load()
        tracker = self._data.get("daily_weather_tracker")
        if not isinstance(tracker, dict):
            tracker = {}
        new_tracker = deepcopy(tracker)
        changed = False
        for key, value in updates.items():
            if new_tracker.get(key) != value:
                new_tracker[key] = value
                changed = True
        if changed:
            self._data["daily_weather_tracker"] = new_tracker
            await self._store.async_save(self._data)
        return deepcopy(new_tracker)

    async def async_update_runtime_config_snapshot(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist planner-facing runtime configuration used during startup restore."""

        await self.async_load()
        new_snapshot = dict(snapshot)
        if self._data.get("runtime_config_snapshot") == new_snapshot:
            return deepcopy(new_snapshot)
        self._data["runtime_config_snapshot"] = new_snapshot
        await self._store.async_save(self._data)
        return deepcopy(new_snapshot)

    async def async_update_zone_runtime_bank(
        self,
        device_id: str,
        zone_number: int,
        *,
        pending_minutes: int,
        last_accumulated_date: str | None,
        last_accumulated_request_minutes: int,
    ) -> None:
        """Persist carryover runtime for a single zone."""

        await self.async_load()
        controller = self._ensure_controller(device_id)
        records = controller.setdefault("zone_runtime_banks", {})
        zone_key = str(zone_number)

        if pending_minutes <= 0:
            if zone_key in records:
                records.pop(zone_key, None)
                await self._store.async_save(self._data)
            return

        new_record = {
            "pending_minutes": int(max(0, pending_minutes)),
            "last_accumulated_date": last_accumulated_date,
            "last_accumulated_request_minutes": int(max(0, last_accumulated_request_minutes)),
        }
        existing = records.get(zone_key)
        if existing == new_record:
            return

        records[zone_key] = new_record
        await self._store.async_save(self._data)

    async def async_upsert_zone_bucket_state(
        self,
        device_id: str,
        zone_number: int,
        *,
        capacity_inches: float,
        current_water_inches: float,
        last_bucket_update: str | None,
        last_et_hour_key: str | None,
        last_authoritative_et_date: str | None,
        last_effective_rain_date: str | None,
        last_effective_rain_total_inches: float,
        last_irrigation_event_key: str | None,
    ) -> None:
        """Persist allowable-depletion bucket state for a single zone."""

        await self.async_load()
        controller = self._ensure_controller(device_id)
        records = controller.setdefault("zone_bucket_states", {})
        zone_key = str(zone_number)
        new_record = {
            "capacity_inches": round(float(capacity_inches), 3),
            "current_water_inches": round(float(current_water_inches), 3),
            "last_bucket_update": last_bucket_update,
            "last_et_hour_key": last_et_hour_key,
            "last_authoritative_et_date": last_authoritative_et_date,
            "last_effective_rain_date": last_effective_rain_date,
            "last_effective_rain_total_inches": round(
                float(last_effective_rain_total_inches),
                3,
            ),
            "last_irrigation_event_key": last_irrigation_event_key,
        }
        if records.get(zone_key) == new_record:
            return
        records[zone_key] = new_record
        await self._store.async_save(self._data)

    async def async_set_controller_weather_stop_hold(
        self,
        device_id: str,
        *,
        date_key: str,
        reason: str,
        wind_speed_mph: float | None,
        wind_gust_mph: float | None,
        effective_wind_threshold_mph: float,
        gust_threshold_mph: float | None,
        effective_wind_profile: str,
        triggered_at: str,
    ) -> None:
        """Persist a controller-level wind stop hold for the local day."""

        await self.async_load()
        controller = self._ensure_controller(device_id)
        new_record = {
            "date": date_key,
            "reason": reason,
            "wind_speed_mph": None if wind_speed_mph is None else round(float(wind_speed_mph), 2),
            "wind_gust_mph": None if wind_gust_mph is None else round(float(wind_gust_mph), 2),
            "effective_wind_threshold_mph": round(float(effective_wind_threshold_mph), 2),
            "gust_threshold_mph": None if gust_threshold_mph is None else round(float(gust_threshold_mph), 2),
            "effective_wind_profile": effective_wind_profile,
            "triggered_at": triggered_at,
        }
        if controller.get("weather_stop_hold") == new_record:
            return
        controller["weather_stop_hold"] = new_record
        await self._store.async_save(self._data)

    async def async_set_zone_weather_stop_hold(
        self,
        device_id: str,
        zone_number: int,
        *,
        date_key: str,
        reason: str,
        wind_speed_mph: float | None,
        wind_gust_mph: float | None,
        effective_wind_threshold_mph: float | None,
        gust_threshold_mph: float | None,
        effective_wind_profile: str,
        triggered_at: str,
    ) -> None:
        """Persist a zone-level weather-stop hold for the local day."""

        await self.async_load()
        controller = self._ensure_controller(device_id)
        records = controller.setdefault("zone_weather_stop_holds", {})
        zone_key = str(zone_number)
        new_record = {
            "date": date_key,
            "reason": reason,
            "wind_speed_mph": None if wind_speed_mph is None else round(float(wind_speed_mph), 2),
            "wind_gust_mph": None if wind_gust_mph is None else round(float(wind_gust_mph), 2),
            "effective_wind_threshold_mph": (
                None
                if effective_wind_threshold_mph is None
                else round(float(effective_wind_threshold_mph), 2)
            ),
            "gust_threshold_mph": None if gust_threshold_mph is None else round(float(gust_threshold_mph), 2),
            "effective_wind_profile": effective_wind_profile,
            "triggered_at": triggered_at,
        }
        if records.get(zone_key) == new_record:
            return
        records[zone_key] = new_record
        await self._store.async_save(self._data)

    async def async_clear_controller_weather_stop_hold(self, device_id: str) -> None:
        """Clear any persisted weather-stop hold for a controller."""

        await self.async_load()
        controller = self._ensure_controller(device_id)
        if "weather_stop_hold" not in controller:
            return
        controller.pop("weather_stop_hold", None)
        await self._store.async_save(self._data)

    async def async_clear_zone_weather_stop_hold(
        self,
        device_id: str,
        zone_number: int | None = None,
    ) -> None:
        """Clear zone-level weather-stop holds for a controller."""

        await self.async_load()
        controller = self._ensure_controller(device_id)
        records = controller.get("zone_weather_stop_holds")
        if not isinstance(records, dict):
            return
        if zone_number is None:
            if not records:
                return
            controller.pop("zone_weather_stop_holds", None)
            await self._store.async_save(self._data)
            return

        zone_key = str(zone_number)
        if zone_key not in records:
            return
        records.pop(zone_key, None)
        if not records:
            controller.pop("zone_weather_stop_holds", None)
        await self._store.async_save(self._data)

    def _ensure_controller(self, device_id: str) -> dict[str, Any]:
        """Return a mutable controller bucket."""

        controllers = self._data.setdefault("controllers", {})
        controller = controllers.get(device_id)
        if not isinstance(controller, dict):
            controller = {}
            controllers[device_id] = controller
        return controller

    @staticmethod
    def _prune_records(records: dict[str, dict[str, float]]) -> None:
        """Keep only a short rolling window of daily records."""

        keep = WATER_BALANCE_WINDOW_DAYS + 7
        for date_key in sorted(records)[:-keep]:
            records.pop(date_key, None)
