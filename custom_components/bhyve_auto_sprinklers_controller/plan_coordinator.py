"""Coordinator for irrigation-planning state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DAILY_RAIN_ENTITY_ID,
    CONF_FORECAST_WEATHER_ENTITY_ID,
    CONF_HUMIDITY_ENTITY_ID,
    CONF_IRRADIANCE_ENTITY_ID,
    CONF_PLANNER_LATITUDE,
    CONF_PLANNER_LONGITUDE,
    CONF_TEMPERATURE_ENTITY_ID,
    CONF_UV_INDEX_ENTITY_ID,
    CONF_WIND_GUST_ENTITY_ID,
    CONF_WIND_SPEED_ENTITY_ID,
    DAILY_RAIN_ROLLOVER_GRACE_HOURS,
    DAILY_RAIN_ROLLOVER_TOLERANCE_INCHES,
    DEFAULT_STARTUP_ENTITY_GRACE_PERIOD,
    DEFAULT_PLAN_SCAN_INTERVAL,
    DEFICIT_SMOOTHING_ALPHA,
    DEFICIT_SMOOTHING_BYPASS_DELTA_INCHES,
    DEFICIT_SMOOTHING_DEADBAND_INCHES,
    DEFICIT_SMOOTHING_NEAR_ZERO_INCHES,
    DOMAIN,
    ET_STALE_THRESHOLD,
    ZONE_WATERING_PROFILE_DEFAULT,
    ZONE_WATERING_PROFILE_DISABLED,
    normalize_zone_watering_profile,
)
from .ledger import BhyveWaterBalanceStore
from .models import (
    BhyveDailyWaterBalance,
    BhyveIrrigationPlanSnapshot,
    BhyveLatestEvent,
    BhyveSprinklersConfigEntry,
    BhyveZoneBucketState,
    merged_zone_recent_events,
)
from .notifications import async_maybe_send_post_sunset_plan_notifications
from .planner import (
    build_controller_plan,
    calc_accumulated_daily_et_inches,
    calc_daily_et_inches,
    calc_effective_rain,
    calc_fao56_daily_reference_et_inches,
    calc_zone_irrigation_inches,
    calc_wind_stop_thresholds,
    clamp_bucket_current_water,
    coerce_zone_bucket_state,
    daylight_gate_hours,
    estimate_legacy_zone_deficit_inches,
    estimate_intraday_reference_et_inches,
    fallback_monthly_solar_wh_m2,
    migrate_bucket_capacity,
    normalize_probability,
    resolve_zone_agronomy,
    resolve_zone_wind_profile,
    zone_hourly_et_inches,
    zone_irrigation_event_key,
)
from .runtime_config import serialize_runtime_config_snapshot

_LOGGER = logging.getLogger(__name__)


class BhyveIrrigationPlanCoordinator(DataUpdateCoordinator[BhyveIrrigationPlanSnapshot]):
    """Refresh derived irrigation recommendations."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BhyveSprinklersConfigEntry,
        water_balance_store: BhyveWaterBalanceStore,
    ) -> None:
        """Initialize the plan coordinator."""

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}_plan",
            update_interval=DEFAULT_PLAN_SCAN_INTERVAL,
        )
        self._entry = entry
        self._water_balance_store = water_balance_store
        self._daily_rain_rollover_date: date | None = None
        self._daily_rain_rollover_baseline_inches: float | None = None
        self._last_daily_rain_source_date: date | None = None
        self._last_daily_rain_source_value: float | None = None
        self._force_authoritative_et_date: date | None = None
        self._automatic_run_unsubscribers: dict[str, Callable[[], None]] = {}
        self._automatic_schedule_tokens: dict[str, str] = {}
        self._automatic_run_in_progress: set[str] = set()
        self._startup_grace_until = dt_util.now() + DEFAULT_STARTUP_ENTITY_GRACE_PERIOD
        self._startup_grace_sunset_retry_unsub: Callable[[], None] | None = None

    async def _async_update_data(self) -> BhyveIrrigationPlanSnapshot:
        """Build the current planning snapshot from live data and persisted weather."""

        await self._water_balance_store.async_load()
        await self._persist_runtime_config_snapshot()
        irrigation_snapshot = self._entry.runtime_data.coordinator.data
        if irrigation_snapshot is None:
            raise UpdateFailed("Irrigation coordinator data is not ready yet")
        previous_controller_plans = {
            controller.device_id: controller
            for controller in (self.data.controllers if self.data is not None else ())
        }
        now_local = dt_util.now()

        daily_rain, weather_status = self._get_daily_rain_inputs()
        yesterday_key = (now_local.date() - timedelta(days=1)).isoformat()
        previous_day_rain_totals = []
        for controller in irrigation_snapshot.controllers:
            records = self._load_daily_records(controller.device_id)
            previous_day_record = self._get_daily_record(records, yesterday_key)
            if previous_day_record is not None:
                previous_day_rain_totals.append(previous_day_record.raw_rain_inches)
        reference_previous_day_rain = (
            max(previous_day_rain_totals) if previous_day_rain_totals else None
        )
        daily_rain = self._normalize_daily_rain_rollover(
            daily_rain,
            today=now_local.date(),
            previous_day_rain_inches=reference_previous_day_rain,
            hour_of_day=now_local.hour,
        )
        daily_rain_tracker = {}
        rain_active_hours_24h = None
        if weather_status == "configured" and daily_rain is not None:
            daily_rain_tracker = await self._water_balance_store.async_observe_daily_rain(
                date_key=now_local.date().isoformat(),
                raw_rain_inches=daily_rain,
                observed_at_iso=now_local.isoformat(),
            )
            if str(daily_rain_tracker.get("date") or "") == now_local.date().isoformat():
                active_seconds = float(daily_rain_tracker.get("rain_active_seconds", 0.0) or 0.0)
                if active_seconds > 0:
                    rain_active_hours_24h = round(active_seconds / 3600.0, 3)
        temperature_f = self._get_numeric_option_state(CONF_TEMPERATURE_ENTITY_ID)
        uv_index = self._get_numeric_option_state(CONF_UV_INDEX_ENTITY_ID)
        solar_radiation_w_m2, solar_radiation_status = self._get_solar_radiation_input()
        humidity_percent = self._get_humidity_input()
        wind_speed_mph = self._get_wind_speed_input()
        wind_gust_mph = self._get_wind_gust_input()
        balance_inputs_ready = self._has_stable_balance_inputs(
            weather_status,
            solar_radiation_status,
        )
        latitude, longitude, location_source = self._resolve_planner_location()
        forecast_amount, forecast_probability, forecast_source = (
            await self._async_get_forecast_inputs()
        )
        hourly_et_inches, et_source = self._resolve_hourly_et_input(
            now_local,
            latitude=latitude,
            temperature_f=temperature_f,
            uv_index=uv_index,
            solar_radiation_w_m2=solar_radiation_w_m2,
            solar_radiation_status=solar_radiation_status,
            humidity_percent=humidity_percent,
            wind_speed_mph=wind_speed_mph,
        )

        controllers = []
        (
            accumulated_et_today_inches,
            _final_et_today_inches,
            _,
            _et_progress_fraction,
        ) = calc_accumulated_daily_et_inches(
            now_local,
            latitude,
            longitude,
            temperature_f,
            uv_index,
            humidity_percent,
            wind_speed_mph,
            solar_radiation_w_m2,
        )
        if self._force_authoritative_et_date == now_local.date():
            accumulated_et_today_inches = _final_et_today_inches

        for controller in irrigation_snapshot.controllers:
            daily_records = self._load_daily_records(controller.device_id)
            today_record = self._get_daily_record(
                daily_records,
                now_local.date().isoformat(),
            )
            plan_daily_rain = float(daily_rain or 0.0)
            plan_et_today_inches = accumulated_et_today_inches
            used_last_good_record = False

            if not balance_inputs_ready and today_record is not None:
                plan_daily_rain = today_record.raw_rain_inches
                plan_et_today_inches = today_record.et_inches
                used_last_good_record = True
            elif today_record is not None:
                plan_et_today_inches = max(
                    float(today_record.et_inches),
                    accumulated_et_today_inches,
                )

            try:
                if not used_last_good_record:
                    await self._water_balance_store.async_upsert_daily_record(
                        controller.device_id,
                        now_local.date().isoformat(),
                        raw_rain_inches=plan_daily_rain,
                        effective_rain_inches=calc_effective_rain(plan_daily_rain),
                        et_inches=plan_et_today_inches,
                    )
                    daily_records = self._load_daily_records(controller.device_id)
                    today_record = self._get_daily_record(
                        daily_records,
                        now_local.date().isoformat(),
                    )
            except Exception as err:
                raise UpdateFailed(f"Unable to persist water-balance history: {err}") from err

            zone_runtime_banks = self._water_balance_store.get_zone_runtime_banks(controller.device_id)
            zone_weather_stop_holds = self._water_balance_store.get_zone_weather_stop_holds(
                controller.device_id
            )
            stale_zone_hold_numbers = [
                int(zone_number)
                for zone_number, hold in zone_weather_stop_holds.items()
                if str(hold.get("date") or "") != now_local.date().isoformat()
            ]
            for zone_number in stale_zone_hold_numbers:
                await self._water_balance_store.async_clear_zone_weather_stop_hold(
                    controller.device_id,
                    zone_number,
                )
                zone_weather_stop_holds.pop(str(zone_number), None)
            zone_bucket_states = await self._async_sync_zone_bucket_states(
                controller=controller,
                now_local=now_local,
                daily_records=daily_records,
                daily_rain_inches=plan_daily_rain,
                rain_active_hours_24h=rain_active_hours_24h,
                hourly_et_inches=hourly_et_inches,
                weekly_target_inches=round(hourly_et_inches * max(daylight_gate_hours()[1] - daylight_gate_hours()[0], 1) * 7, 3),
                effective_rain_7d_inches=round(
                    sum(record.effective_rain_inches for record in daily_records[-7:]),
                    3,
                ),
                et_7d_inches=round(sum(record.et_inches for record in daily_records[-7:]), 3),
                overall_watering_coefficient=self._entry.runtime_data.overall_watering_coefficient,
                zone_application_rates=self._entry.runtime_data.zone_application_rates,
                zone_watering_coefficients=self._entry.runtime_data.zone_watering_coefficients,
                zone_watering_profiles=self._entry.runtime_data.zone_watering_profiles,
                zone_root_depths=self._entry.runtime_data.zone_root_depths,
                zone_soil_whc=self._entry.runtime_data.zone_soil_whc,
                zone_mad_values=self._entry.runtime_data.zone_mad_values,
                zone_kc_values=self._entry.runtime_data.zone_kc_values,
                zone_trigger_buffers=self._entry.runtime_data.zone_trigger_buffers,
            )
            plan = build_controller_plan(
                controller=controller,
                now_local=now_local,
                daily_records=daily_records,
                daily_rain_inches=plan_daily_rain,
                rain_active_hours_24h=rain_active_hours_24h,
                latitude=latitude,
                longitude=longitude,
                location_source=location_source,
                temperature_f=temperature_f,
                uv_index=uv_index,
                irradiance_w_m2=solar_radiation_w_m2,
                humidity_percent=humidity_percent,
                wind_speed_mph=wind_speed_mph,
                wind_gust_mph=wind_gust_mph,
                forecast_rain_amount_inches=forecast_amount,
                forecast_rain_probability=forecast_probability,
                overall_watering_coefficient=self._entry.runtime_data.overall_watering_coefficient,
                minimum_run_threshold_minutes=self._entry.runtime_data.minimum_run_threshold_minutes,
                max_watering_wind_speed_mph=self._entry.runtime_data.max_watering_wind_speed_mph,
                min_watering_temperature_f=self._entry.runtime_data.min_watering_temperature_f,
                zone_application_rates=self._entry.runtime_data.zone_application_rates,
                max_weekly_runtime_minutes=self._entry.runtime_data.max_weekly_run_times,
                zone_watering_coefficients=self._entry.runtime_data.zone_watering_coefficients,
                zone_watering_profiles=self._entry.runtime_data.zone_watering_profiles,
                zone_sprinkler_wind_profiles=self._entry.runtime_data.zone_sprinkler_wind_profiles,
                controller_watering_day_restrictions=self._entry.runtime_data.controller_watering_day_restrictions,
                zone_watering_day_restrictions=self._entry.runtime_data.zone_watering_day_restrictions,
                zone_runtime_banks=zone_runtime_banks,
                start_time_by_device=self._entry.runtime_data.watering_window_times,
                end_time_by_device=self._entry.runtime_data.watering_window_times,
                automatic_window_enabled_by_device=self._entry.runtime_data.automatic_window_enabled,
                automatic_window_preference_by_device=self._entry.runtime_data.automatic_window_preferences,
                automatic_window_max_minutes_by_device=self._entry.runtime_data.automatic_window_max_minutes,
                et_today_override_inches=plan_et_today_inches,
                zone_weather_stop_holds=zone_weather_stop_holds,
                zone_bucket_states=zone_bucket_states,
                zone_root_depths=self._entry.runtime_data.zone_root_depths,
                zone_soil_whc=self._entry.runtime_data.zone_soil_whc,
                zone_mad_values=self._entry.runtime_data.zone_mad_values,
                zone_kc_values=self._entry.runtime_data.zone_kc_values,
                zone_trigger_buffers=self._entry.runtime_data.zone_trigger_buffers,
                hourly_et_inches=hourly_et_inches,
                et_source=et_source,
            )
            plan = self._smooth_controller_plan(
                plan,
                previous_controller_plans.get(controller.device_id),
            )
            if weather_status == "daily_rain_missing":
                plan = replace(
                    plan,
                    decision="not_configured",
                    reason=(
                        "Select a daily rain source on the B-hyve Account configuration "
                        "card to enable scheduling."
                    ),
                    weather_source_status=weather_status,
                )
            elif solar_radiation_status == "solar_radiation_missing":
                plan = replace(
                    plan,
                    decision="not_configured",
                    reason=(
                        "Select a solar radiation source (W/m²) on the B-hyve Account "
                        "configuration card to enable ET-based planning."
                    ),
                    weather_source_status=solar_radiation_status,
                )
            elif solar_radiation_status == "solar_radiation_unavailable":
                plan = replace(
                    plan,
                    decision="not_configured",
                    reason=(
                        "Solar radiation is unavailable right now, so watering decisions "
                        "are paused until that sensor returns."
                    ),
                    weather_source_status=solar_radiation_status,
                )
            else:
                plan = replace(
                    plan,
                    weather_source_status=(
                        "restored_from_last_good_record"
                        if used_last_good_record
                        else weather_status
                    ),
                )

            controllers.append(plan)

        snapshot = BhyveIrrigationPlanSnapshot(
            controllers=tuple(controllers),
            forecast_source=forecast_source,
            forecast_rain_amount_inches=forecast_amount,
            forecast_rain_probability=normalize_probability(forecast_probability),
            weather_source_status=(
                weather_status
                if weather_status != "configured"
                else solar_radiation_status
            ),
        )
        if self._startup_grace_active(now_local):
            self.async_clear_automatic_run_schedules(cancel_startup_retry=False)
            _LOGGER.debug(
                "Skipping B-hyve planner notifications and automatic watering until startup entity grace ends at %s",
                self._startup_grace_until.isoformat(),
            )
        else:
            await async_maybe_send_post_sunset_plan_notifications(
                self._entry,
                snapshot.controllers,
                now_local=now_local,
                latitude=latitude,
                longitude=longitude,
            )
            self._async_schedule_automatic_runs(snapshot, now_local)
        return snapshot

    async def async_refresh_for_sunset_notification(
        self,
        event_time: datetime | None = None,
    ) -> None:
        """Refresh the plan at sunset and send the daily summary immediately."""

        try:
            local_event_time = (
                dt_util.as_local(event_time)
                if event_time is not None
                else dt_util.now()
            )
            if self._startup_grace_active(dt_util.now()):
                self._schedule_sunset_retry_after_startup_grace(local_event_time)
                return

            self._force_authoritative_et_date = local_event_time.date()
            await self.async_request_refresh()
        except Exception:
            _LOGGER.warning(
                "Unable to refresh the irrigation plan for the sunset notification",
                exc_info=True,
            )
            return
        finally:
            self._force_authoritative_et_date = None

        snapshot = self.data
        if snapshot is None:
            return

        now_local = local_event_time
        if self._startup_grace_active(dt_util.now()):
            self._schedule_sunset_retry_after_startup_grace(now_local)
            return

        latitude, longitude, _location_source = self._resolve_planner_location()
        await async_maybe_send_post_sunset_plan_notifications(
            self._entry,
            snapshot.controllers,
            now_local=now_local,
            latitude=latitude,
            longitude=longitude,
            force=True,
        )

    def async_clear_automatic_run_schedules(
        self,
        *,
        cancel_startup_retry: bool = True,
    ) -> None:
        """Cancel pending automatic watering callbacks for this entry."""

        for device_id in list(self._automatic_run_unsubscribers):
            self._clear_automatic_run_schedule(device_id)
        self._automatic_schedule_tokens.clear()
        if cancel_startup_retry and self._startup_grace_sunset_retry_unsub is not None:
            self._startup_grace_sunset_retry_unsub()
            self._startup_grace_sunset_retry_unsub = None

    def _async_schedule_automatic_runs(
        self,
        snapshot: BhyveIrrigationPlanSnapshot,
        now_local: datetime,
    ) -> None:
        """Schedule automatic runs from the current planner snapshot."""

        if self._startup_grace_active(now_local):
            self.async_clear_automatic_run_schedules(cancel_startup_retry=False)
            return

        if not self._entry.runtime_data.automatic_watering_enabled:
            self.async_clear_automatic_run_schedules()
            return

        active_device_ids: set[str] = set()
        for controller_plan in snapshot.controllers:
            device_id = controller_plan.device_id
            active_device_ids.add(device_id)
            if device_id in self._automatic_run_in_progress:
                continue

            zone_runs = self._zone_runs_for_plan(controller_plan)
            window = self._automatic_run_window(controller_plan)
            if controller_plan.decision != "run" or not zone_runs or window is None:
                self._clear_automatic_run_schedule(device_id)
                continue

            start_dt, end_dt = window
            if end_dt <= now_local:
                self._clear_automatic_run_schedule(device_id)
                continue

            run_token = self._automatic_run_token(controller_plan, start_dt, zone_runs)
            if self._entry.runtime_data.automatic_run_tokens.get(device_id) == run_token:
                self._clear_automatic_run_schedule(device_id)
                continue

            self._schedule_automatic_run_at(
                controller_plan=controller_plan,
                schedule_time=max(start_dt, now_local),
                run_token=run_token,
            )

        for device_id in list(self._automatic_run_unsubscribers):
            if device_id not in active_device_ids:
                self._clear_automatic_run_schedule(device_id)

    def _schedule_automatic_run_at(
        self,
        *,
        controller_plan,
        schedule_time: datetime,
        run_token: str,
    ) -> None:
        """Schedule one controller's next automatic run."""

        device_id = controller_plan.device_id
        if self._automatic_schedule_tokens.get(device_id) == run_token:
            return

        self._clear_automatic_run_schedule(device_id)
        self._automatic_schedule_tokens[device_id] = run_token

        if schedule_time <= dt_util.now() + timedelta(seconds=1):
            self.hass.async_create_task(
                self._async_run_scheduled_automatic_cycle(device_id, run_token)
            )
            return

        @callback
        def _async_handle_automatic_run(_now: datetime) -> None:
            self.hass.async_create_task(
                self._async_run_scheduled_automatic_cycle(device_id, run_token)
            )

        self._automatic_run_unsubscribers[device_id] = async_track_point_in_utc_time(
            self.hass,
            _async_handle_automatic_run,
            dt_util.as_utc(schedule_time),
        )

        _LOGGER.debug(
            "Scheduled automatic watering for %s at %s",
            controller_plan.nickname,
            schedule_time.isoformat(),
        )

    async def _async_run_scheduled_automatic_cycle(
        self,
        device_id: str,
        expected_token: str,
    ) -> None:
        """Refresh the plan at the scheduled window start and run due zones."""

        if self._automatic_schedule_tokens.get(device_id) != expected_token:
            return
        self._clear_automatic_run_schedule(device_id)

        if device_id in self._automatic_run_in_progress:
            return
        self._automatic_run_in_progress.add(device_id)
        try:
            if not self._entry.runtime_data.automatic_watering_enabled:
                return
            if self._entry.runtime_data.automatic_run_tokens.get(device_id) == expected_token:
                return

            coordinator = self._entry.runtime_data.coordinator
            controller = coordinator.get_controller(device_id)
            if controller is not None and controller.active_run is not None:
                _LOGGER.info(
                    "Skipping automatic watering for %s because watering is already active",
                    controller.nickname,
                )
                return

            try:
                await coordinator.async_request_refresh()
                controller = coordinator.get_controller(device_id)
                if controller is not None and controller.active_run is not None:
                    _LOGGER.info(
                        "Skipping automatic watering for %s because watering is already active",
                        controller.nickname,
                    )
                    return
                await self.async_request_refresh()
            except Exception:
                _LOGGER.warning(
                    "Unable to refresh the irrigation plan for automatic watering",
                    exc_info=True,
                )
                return

            controller_plan = self.get_controller_plan(device_id)
            if controller_plan is None or controller_plan.decision != "run":
                return

            window = self._automatic_run_window(controller_plan)
            if window is None:
                return
            start_dt, end_dt = window
            now_local = dt_util.now()
            if now_local < start_dt:
                self._schedule_automatic_run_at(
                    controller_plan=controller_plan,
                    schedule_time=start_dt,
                    run_token=self._automatic_run_token(
                        controller_plan,
                        start_dt,
                        self._zone_runs_for_plan(controller_plan),
                    ),
                )
                return
            if now_local > end_dt:
                return

            zone_runs = self._zone_runs_for_plan(controller_plan)
            if not zone_runs:
                return
            run_token = self._automatic_run_token(controller_plan, start_dt, zone_runs)
            if self._entry.runtime_data.automatic_run_tokens.get(device_id) == run_token:
                return

            try:
                await coordinator.async_run_zone_sequence(
                    device_id,
                    zone_runs,
                    source="automatic_window",
                )
            except Exception:
                _LOGGER.warning(
                    "Unable to start the automatic watering run for %s",
                    controller_plan.nickname,
                    exc_info=True,
                )
                return

            self._entry.runtime_data.automatic_run_tokens[device_id] = run_token
        finally:
            self._automatic_run_in_progress.discard(device_id)

    def _clear_automatic_run_schedule(self, device_id: str) -> None:
        """Cancel a pending automatic watering callback for one controller."""

        unsub = self._automatic_run_unsubscribers.pop(device_id, None)
        if unsub is not None:
            unsub()
        self._automatic_schedule_tokens.pop(device_id, None)

    def _startup_grace_active(self, now_local: datetime | None = None) -> bool:
        """Return whether HA is still restoring configured helper entities."""

        now_local = now_local or dt_util.now()
        return now_local < self._startup_grace_until

    def _schedule_sunset_retry_after_startup_grace(
        self,
        event_time: datetime,
    ) -> None:
        """Retry the sunset plan refresh once startup entities have restored."""

        if self._startup_grace_sunset_retry_unsub is not None:
            return

        retry_time = max(
            self._startup_grace_until,
            dt_util.now() + timedelta(seconds=1),
        )

        @callback
        def _async_handle_retry(_now: datetime) -> None:
            self._startup_grace_sunset_retry_unsub = None
            self.hass.async_create_task(
                self.async_refresh_for_sunset_notification(event_time)
            )

        self._startup_grace_sunset_retry_unsub = async_track_point_in_utc_time(
            self.hass,
            _async_handle_retry,
            dt_util.as_utc(retry_time),
        )
        _LOGGER.debug(
            "Deferring B-hyve sunset plan notification until startup entity grace ends at %s",
            retry_time.isoformat(),
        )

    @staticmethod
    def _zone_runs_for_plan(controller_plan) -> list[tuple[int, int]]:
        """Return the planned zone sequence for a controller plan."""

        zone_runs: list[tuple[int, int]] = []
        for zone_plan in controller_plan.zone_plans:
            for segment in zone_plan.cycle_minutes:
                if segment > 0:
                    zone_runs.append((zone_plan.zone_number, int(segment * 60)))
        return zone_runs

    @staticmethod
    def _automatic_run_window(controller_plan) -> tuple[datetime, datetime] | None:
        """Return the projected automatic run window as local datetimes."""

        if controller_plan.next_cycle_start is None or controller_plan.next_cycle_end is None:
            return None
        start_dt = dt_util.parse_datetime(controller_plan.next_cycle_start)
        end_dt = dt_util.parse_datetime(controller_plan.next_cycle_end)
        if start_dt is None or end_dt is None:
            return None
        return dt_util.as_local(start_dt), dt_util.as_local(end_dt)

    @staticmethod
    def _automatic_run_token(
        controller_plan,
        start_dt: datetime,
        zone_runs: list[tuple[int, int]],
    ) -> str:
        """Return a stable once-per-window automatic run token."""

        return (
            f"{start_dt.date().isoformat()}:"
            f"{controller_plan.device_id}:"
            f"{start_dt.strftime('%H:%M')}:"
            f"{tuple(zone_runs)}"
        )

    async def _persist_runtime_config_snapshot(self) -> None:
        """Persist restore-backed planner config so restart refreshes stay stable."""

        await self._water_balance_store.async_update_runtime_config_snapshot(
            serialize_runtime_config_snapshot(self._entry.runtime_data)
        )

    async def _async_sync_zone_bucket_states(
        self,
        *,
        controller,
        now_local: datetime,
        daily_records: tuple[BhyveDailyWaterBalance, ...],
        daily_rain_inches: float,
        rain_active_hours_24h: float | None,
        hourly_et_inches: float,
        weekly_target_inches: float,
        effective_rain_7d_inches: float,
        et_7d_inches: float,
        overall_watering_coefficient: float,
        zone_application_rates: dict[str, float],
        zone_watering_coefficients: dict[str, float],
        zone_watering_profiles: dict[str, str],
        zone_root_depths: dict[str, float],
        zone_soil_whc: dict[str, float],
        zone_mad_values: dict[str, float],
        zone_kc_values: dict[str, float],
        zone_trigger_buffers: dict[str, float],
    ) -> dict[str, BhyveZoneBucketState]:
        """Bring persisted bucket state forward using rain, irrigation, and hourly ET."""

        persisted_bucket_states = self._water_balance_store.get_zone_bucket_states(
            controller.device_id
        )
        today_key = now_local.date().isoformat()
        effective_rain_today_inches = calc_effective_rain(
            daily_rain_inches,
            rain_active_hours_24h,
        )
        synced_states: dict[str, BhyveZoneBucketState] = {}

        for zone in controller.zones:
            runtime_key = f"{controller.device_id}:{zone.zone_number}"
            watering_profile = normalize_zone_watering_profile(
                zone_watering_profiles.get(runtime_key, ZONE_WATERING_PROFILE_DEFAULT)
            )
            user_watering_coefficient = float(
                zone_watering_coefficients.get(runtime_key, 1.0) or 1.0
            )
            application_rate_inches_per_hour = round(
                max(0.0, float(zone_application_rates.get(runtime_key, 0.0) or 0.0)),
                2,
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
            capacity_inches = float(agronomy["capacity_in"])
            kc = float(agronomy["kc"])
            bucket_state = coerce_zone_bucket_state(
                persisted_bucket_states.get(str(zone.zone_number))
                or persisted_bucket_states.get(runtime_key),
                capacity_inches=capacity_inches,
            )

            zone_hourly_et = self._zone_hourly_et_for_bucket(
                zone=zone,
                hourly_et_inches=hourly_et_inches,
                kc=kc,
                overall_watering_coefficient=overall_watering_coefficient,
                zone_watering_coefficient=user_watering_coefficient,
            )

            if bucket_state is None:
                bucket_state = self._bootstrap_zone_bucket_state(
                    zone=zone,
                    now_local=now_local,
                    application_rate_inches_per_hour=application_rate_inches_per_hour,
                    effective_rain_7d_inches=effective_rain_7d_inches,
                    et_7d_inches=et_7d_inches,
                    weekly_target_inches=weekly_target_inches,
                    overall_watering_coefficient=overall_watering_coefficient,
                    zone_watering_coefficient=user_watering_coefficient,
                    kc=kc,
                    root_depth_inches=float(agronomy["root_depth_in"]),
                    soil_whc_in_per_in=float(agronomy["soil_whc_in_per_in"]),
                    mad=float(agronomy["mad"]),
                    capacity_inches=capacity_inches,
                    zone_hourly_et_inches=zone_hourly_et,
                    effective_rain_today_inches=effective_rain_today_inches,
                )
            else:
                bucket_state = self._migrate_bucket_capacity_if_needed(
                    zone_name=zone.name,
                    bucket_state=bucket_state,
                    capacity_inches=capacity_inches,
                )

            if watering_profile == ZONE_WATERING_PROFILE_DISABLED:
                latest_event_key = bucket_state.last_irrigation_event_key
                latest_events = merged_zone_recent_events(zone)
                if latest_events:
                    latest_event_key = zone_irrigation_event_key(latest_events[0])
                current_hour_start = now_local.replace(minute=0, second=0, microsecond=0)
                synced_state = BhyveZoneBucketState(
                    capacity_inches=round(capacity_inches, 3),
                    current_water_inches=clamp_bucket_current_water(
                        capacity_inches,
                        capacity_inches,
                    ),
                    last_bucket_update=now_local.isoformat(),
                    last_et_hour_key=(
                        current_hour_start - timedelta(hours=1)
                    ).strftime("%Y-%m-%dT%H"),
                    last_authoritative_et_date=bucket_state.last_authoritative_et_date,
                    last_effective_rain_date=today_key,
                    last_effective_rain_total_inches=round(
                        max(0.0, float(effective_rain_today_inches)),
                        3,
                    ),
                    last_irrigation_event_key=latest_event_key,
                )
                synced_states[runtime_key] = synced_state
                await self._water_balance_store.async_upsert_zone_bucket_state(
                    controller.device_id,
                    zone.zone_number,
                    capacity_inches=synced_state.capacity_inches,
                    current_water_inches=synced_state.current_water_inches,
                    last_bucket_update=synced_state.last_bucket_update,
                    last_et_hour_key=synced_state.last_et_hour_key,
                    last_authoritative_et_date=synced_state.last_authoritative_et_date,
                    last_effective_rain_date=synced_state.last_effective_rain_date,
                    last_effective_rain_total_inches=synced_state.last_effective_rain_total_inches,
                    last_irrigation_event_key=synced_state.last_irrigation_event_key,
                )
                continue

            current_water_inches = clamp_bucket_current_water(
                bucket_state.current_water_inches,
                capacity_inches,
            )
            (
                current_water_inches,
                last_effective_rain_date,
                last_effective_rain_total_inches,
            ) = self._apply_effective_rain_to_bucket(
                current_water_inches=current_water_inches,
                capacity_inches=capacity_inches,
                bucket_state=bucket_state,
                today_key=today_key,
                effective_rain_today_inches=effective_rain_today_inches,
            )
            current_water_inches, last_irrigation_event_key = self._apply_irrigation_to_bucket(
                zone=zone,
                now_local=now_local,
                current_water_inches=current_water_inches,
                capacity_inches=capacity_inches,
                bucket_state=bucket_state,
                application_rate_inches_per_hour=application_rate_inches_per_hour,
            )
            current_water_inches, last_et_hour_key = self._apply_hourly_et_to_bucket(
                now_local=now_local,
                current_water_inches=current_water_inches,
                capacity_inches=capacity_inches,
                bucket_state=bucket_state,
                zone_hourly_et_inches=zone_hourly_et,
            )

            synced_state = BhyveZoneBucketState(
                capacity_inches=round(capacity_inches, 3),
                current_water_inches=clamp_bucket_current_water(
                    current_water_inches,
                    capacity_inches,
                ),
                last_bucket_update=now_local.isoformat(),
                last_et_hour_key=last_et_hour_key,
                last_authoritative_et_date=bucket_state.last_authoritative_et_date,
                last_effective_rain_date=last_effective_rain_date,
                last_effective_rain_total_inches=last_effective_rain_total_inches,
                last_irrigation_event_key=last_irrigation_event_key,
            )
            synced_states[runtime_key] = synced_state
            await self._water_balance_store.async_upsert_zone_bucket_state(
                controller.device_id,
                zone.zone_number,
                capacity_inches=synced_state.capacity_inches,
                current_water_inches=synced_state.current_water_inches,
                last_bucket_update=synced_state.last_bucket_update,
                last_et_hour_key=synced_state.last_et_hour_key,
                last_authoritative_et_date=synced_state.last_authoritative_et_date,
                last_effective_rain_date=synced_state.last_effective_rain_date,
                last_effective_rain_total_inches=synced_state.last_effective_rain_total_inches,
                last_irrigation_event_key=synced_state.last_irrigation_event_key,
            )

        return synced_states

    def _bootstrap_zone_bucket_state(
        self,
        *,
        zone,
        now_local: datetime,
        application_rate_inches_per_hour: float,
        effective_rain_7d_inches: float,
        et_7d_inches: float,
        weekly_target_inches: float,
        overall_watering_coefficient: float,
        zone_watering_coefficient: float,
        kc: float,
        root_depth_inches: float,
        soil_whc_in_per_in: float,
        mad: float,
        capacity_inches: float,
        zone_hourly_et_inches: float,
        effective_rain_today_inches: float,
    ) -> BhyveZoneBucketState:
        """Seed a zone bucket from the legacy rolling history on first use."""

        since_utc = (now_local - timedelta(days=7)).astimezone()
        legacy_deficit_inches, _, _, _, _ = estimate_legacy_zone_deficit_inches(
            zone=zone,
            since_utc=since_utc,
            effective_rain_7d_inches=effective_rain_7d_inches,
            et_7d_inches=et_7d_inches,
            zone_application_rate_inches_per_hour=application_rate_inches_per_hour,
            weekly_target_inches=weekly_target_inches,
            overall_watering_coefficient=overall_watering_coefficient,
            zone_watering_coefficient=zone_watering_coefficient,
            kc=kc,
        )
        current_hour_start = now_local.replace(minute=0, second=0, microsecond=0)
        partial_hour_fraction = self._current_daylight_hour_fraction(now_local)
        bootstrapped_water_inches = clamp_bucket_current_water(
            capacity_inches - legacy_deficit_inches + (zone_hourly_et_inches * partial_hour_fraction),
            capacity_inches,
        )
        latest_event_key = None
        latest_events = merged_zone_recent_events(zone)
        if latest_events:
            latest_event_key = zone_irrigation_event_key(latest_events[0])

        _LOGGER.info(
            "%s: capacity=%.2fin  (root=%.2fin, WHC=%.3f in/in, MAD=%.3f, kc=%.3f)",
            zone.name,
            capacity_inches,
            root_depth_inches,
            soil_whc_in_per_in,
            mad,
            kc,
        )
        return BhyveZoneBucketState(
            capacity_inches=round(capacity_inches, 3),
            current_water_inches=bootstrapped_water_inches,
            last_bucket_update=now_local.isoformat(),
            last_et_hour_key=(current_hour_start - timedelta(hours=1)).strftime("%Y-%m-%dT%H"),
            last_authoritative_et_date=None,
            last_effective_rain_date=now_local.date().isoformat(),
            last_effective_rain_total_inches=round(float(effective_rain_today_inches), 3),
            last_irrigation_event_key=latest_event_key,
        )

    def _migrate_bucket_capacity_if_needed(
        self,
        *,
        zone_name: str,
        bucket_state: BhyveZoneBucketState,
        capacity_inches: float,
    ) -> BhyveZoneBucketState:
        """Preserve fill ratio when a zone's capacity changes."""

        migrated_state, migration = migrate_bucket_capacity(
            bucket_state,
            capacity_inches=capacity_inches,
        )
        if not bool(migration["changed"]):
            return bucket_state

        _LOGGER.info(
            "%s: capacity migration old=%.2fin -> new=%.2fin, water old=%.2fin -> new=%.2fin, preserved fill ratio=%.3f",
            zone_name,
            float(migration["old_capacity_inches"]),
            float(migration["new_capacity_inches"]),
            float(migration["old_water_inches"]),
            float(migration["new_water_inches"]),
            float(migration["fill_ratio"]),
        )
        if bool(migration["clamped_to_full"]):
            _LOGGER.warning(
                "%s: capacity decreased below the preserved water level, so the bucket was clamped to full at %.2f in",
                zone_name,
                float(migration["new_capacity_inches"]),
            )
        return migrated_state

    def _apply_effective_rain_to_bucket(
        self,
        *,
        current_water_inches: float,
        capacity_inches: float,
        bucket_state: BhyveZoneBucketState,
        today_key: str,
        effective_rain_today_inches: float,
    ) -> tuple[float, str, float]:
        """Apply only positive effective-rain deltas to the current bucket."""

        previous_total_inches = round(float(bucket_state.last_effective_rain_total_inches), 3)
        if bucket_state.last_effective_rain_date != today_key:
            rain_delta_inches = max(0.0, float(effective_rain_today_inches))
            updated_total_inches = round(max(0.0, float(effective_rain_today_inches)), 3)
        else:
            rain_delta_inches = max(
                0.0,
                round(float(effective_rain_today_inches) - previous_total_inches, 3),
            )
            updated_total_inches = round(
                max(previous_total_inches, float(effective_rain_today_inches)),
                3,
            )
        if rain_delta_inches > 0:
            current_water_inches = clamp_bucket_current_water(
                current_water_inches + rain_delta_inches,
                capacity_inches,
            )
        return current_water_inches, today_key, updated_total_inches

    def _apply_irrigation_to_bucket(
        self,
        *,
        zone,
        now_local: datetime,
        current_water_inches: float,
        capacity_inches: float,
        bucket_state: BhyveZoneBucketState,
        application_rate_inches_per_hour: float,
    ) -> tuple[float, str | None]:
        """Apply new completed irrigation events once using the measured application rate."""

        events = [
            event
            for event in merged_zone_recent_events(zone)
            if event.end_ts is not None and event.duration is not None
        ]
        if not events:
            return current_water_inches, bucket_state.last_irrigation_event_key

        stored_event_key = bucket_state.last_irrigation_event_key
        stored_event_ts = self._irrigation_event_ts_from_key(stored_event_key)

        new_events: list[BhyveLatestEvent] = []
        if stored_event_key is None:
            new_events = [
                event for event in events if zone_irrigation_event_key(event) is not None
            ]
        else:
            for event in events:
                event_key = zone_irrigation_event_key(event)
                if event_key is None:
                    continue
                if event_key == stored_event_key:
                    break
                if (
                    stored_event_ts is not None
                    and event.end_ts is not None
                    and event.end_ts <= stored_event_ts
                ):
                    continue
                new_events.append(event)

        for event in reversed(new_events):
            inches_applied = calc_zone_irrigation_inches(
                application_rate_inches_per_hour,
                event.duration / 60.0,
            )
            if inches_applied is None or inches_applied <= 0:
                continue
            current_water_inches = clamp_bucket_current_water(
                current_water_inches + inches_applied,
                capacity_inches,
            )

        latest_event_key = (
            zone_irrigation_event_key(events[0]) if new_events else stored_event_key
        )
        return current_water_inches, latest_event_key

    @staticmethod
    def _irrigation_event_ts_from_key(event_key: str | None) -> int | None:
        """Extract the event end timestamp from a persisted irrigation marker."""

        if not event_key:
            return None
        try:
            return int(str(event_key).split(":", 1)[0])
        except (TypeError, ValueError):
            return None

    def _apply_hourly_et_to_bucket(
        self,
        *,
        now_local: datetime,
        current_water_inches: float,
        capacity_inches: float,
        bucket_state: BhyveZoneBucketState,
        zone_hourly_et_inches: float,
    ) -> tuple[float, str | None]:
        """Apply unseen completed daylight hours of ET exactly once."""

        last_hour_key = bucket_state.last_et_hour_key
        current_hour_start = now_local.replace(minute=0, second=0, microsecond=0)
        if last_hour_key is None:
            return current_water_inches, (current_hour_start - timedelta(hours=1)).strftime("%Y-%m-%dT%H")

        last_hour_start = self._hour_start_from_key(last_hour_key, now_local)
        if last_hour_start is None:
            return current_water_inches, (current_hour_start - timedelta(hours=1)).strftime("%Y-%m-%dT%H")

        updated_hour_key = last_hour_key
        cursor = last_hour_start + timedelta(hours=1)
        while cursor < current_hour_start:
            updated_hour_key = cursor.strftime("%Y-%m-%dT%H")
            if self._is_daylight_hour(cursor):
                current_water_inches = clamp_bucket_current_water(
                    current_water_inches - zone_hourly_et_inches,
                    capacity_inches,
                )
            cursor += timedelta(hours=1)
        return current_water_inches, updated_hour_key

    def _zone_hourly_et_for_bucket(
        self,
        *,
        zone,
        hourly_et_inches: float,
        kc: float,
        overall_watering_coefficient: float,
        zone_watering_coefficient: float,
    ) -> float:
        """Return the per-zone hourly ET draw used by the bucket model."""

        return zone_hourly_et_inches(
            hourly_et_inches=hourly_et_inches,
            kc=kc,
            exposure_factor=self._zone_exposure_factor(zone),
            overall_watering_coefficient=overall_watering_coefficient,
            zone_watering_coefficient=zone_watering_coefficient,
        )

    def _resolve_hourly_et_input(
        self,
        now_local: datetime,
        *,
        latitude: float,
        temperature_f: float | None,
        uv_index: float | None,
        solar_radiation_w_m2: float | None,
        solar_radiation_status: str,
        humidity_percent: float | None,
        wind_speed_mph: float | None,
    ) -> tuple[float, str]:
        """Return the hourly ET input to use for bucket accrual and projection."""

        if solar_radiation_status != "configured" or solar_radiation_w_m2 is None:
            return 0.0, solar_radiation_status

        computed_daily_et_inches, _ = calc_daily_et_inches(
            now_local.date(),
            latitude,
            temperature_f,
            uv_index,
            humidity_percent,
            wind_speed_mph,
            solar_radiation_w_m2,
        )
        daylight_start, daylight_end = daylight_gate_hours()
        daylight_hours = max(1, daylight_end - daylight_start)
        return (
            round(max(0.0, computed_daily_et_inches) / daylight_hours, 4),
            "computed_from_weather_inputs",
        )

    def _get_solar_radiation_input(self) -> tuple[float | None, str]:
        """Return solar radiation in W/m² plus configuration status."""

        entity_id = self._entry.options.get(CONF_IRRADIANCE_ENTITY_ID)
        if not entity_id:
            return None, "solar_radiation_missing"

        state = self.hass.states.get(str(entity_id))
        if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            return None, "solar_radiation_unavailable"

        try:
            return max(0.0, float(state.state)), "configured"
        except (TypeError, ValueError):
            return None, "solar_radiation_unavailable"

    @staticmethod
    def _hour_start_from_key(hour_key: str, now_local: datetime) -> datetime | None:
        """Parse a persisted local hour watermark key."""

        try:
            parsed = datetime.strptime(hour_key, "%Y-%m-%dT%H")
        except (TypeError, ValueError):
            return None
        return parsed.replace(tzinfo=now_local.tzinfo)

    @staticmethod
    def _current_daylight_hour_fraction(now_local: datetime) -> float:
        """Return the elapsed fraction of the current daylight hour."""

        daylight_start, daylight_end = daylight_gate_hours()
        if not daylight_start <= now_local.hour < daylight_end:
            return 0.0
        return round(
            (
                now_local.minute
                + (now_local.second / 60.0)
                + (now_local.microsecond / 60000000.0)
            )
            / 60.0,
            4,
        )

    @staticmethod
    def _is_daylight_hour(hour_start: datetime) -> bool:
        """Return True when an hourly bucket accrual slot is inside the ET gate."""

        daylight_start, daylight_end = daylight_gate_hours()
        return daylight_start <= hour_start.hour < daylight_end

    @staticmethod
    def _zone_exposure_factor(zone) -> float:
        """Return the zone exposure factor without importing the entire planner namespace."""

        exposure = (zone.exposure_type or "").upper()
        if exposure == "LOTS_OF_SUN":
            return 1.08
        if exposure == "SOME_SHADE":
            return 0.88
        if exposure == "MOSTLY_SHADE":
            return 0.72
        return 1.0

    def get_live_wind_speed_mph(self) -> float | None:
        """Return the current configured average wind speed in mph."""

        return self._get_wind_speed_input()

    def get_live_wind_gust_mph(self) -> float | None:
        """Return the current configured wind gust in mph."""

        return self._get_wind_gust_input()

    def get_effective_wind_settings(
        self,
        device_id: str,
        *,
        active_zone_number: int | None = None,
    ) -> dict[str, float | str | None]:
        """Return effective wind-profile thresholds for a controller or active zone."""

        controller = self._entry.runtime_data.coordinator.get_controller(device_id)
        if controller is None:
            return {
                "sprinkler_wind_profile": "Standard spray",
                "effective_wind_profile": "Standard spray",
                "effective_max_watering_wind_speed_mph": self._entry.runtime_data.max_watering_wind_speed_mph,
                "max_watering_gust_speed_mph": None,
            }

        runtime_key = None
        if active_zone_number is not None:
            runtime_key = f"{device_id}:{active_zone_number}"
        effective_profile = resolve_zone_wind_profile(
            self._entry.runtime_data.zone_sprinkler_wind_profiles.get(runtime_key)
            if runtime_key is not None
            else None
        )
        effective_threshold, gust_threshold = calc_wind_stop_thresholds(
            self._entry.runtime_data.max_watering_wind_speed_mph,
            effective_profile,
        )
        return {
            "sprinkler_wind_profile": effective_profile,
            "effective_wind_profile": effective_profile,
            "effective_max_watering_wind_speed_mph": effective_threshold,
            "max_watering_gust_speed_mph": gust_threshold,
        }

    def get_runtime_wind_stop_reason(
        self,
        device_id: str,
        *,
        active_zone_number: int | None = None,
    ) -> dict[str, float | str | None] | None:
        """Return a live wind-stop reason for an active watering run when needed."""

        wind_speed_mph = self.get_live_wind_speed_mph()
        wind_gust_mph = self.get_live_wind_gust_mph()
        if wind_speed_mph is None and wind_gust_mph is None:
            return None

        settings = self.get_effective_wind_settings(
            device_id,
            active_zone_number=active_zone_number,
        )
        effective_threshold = settings["effective_max_watering_wind_speed_mph"]
        gust_threshold = settings["max_watering_gust_speed_mph"]
        if (
            wind_speed_mph is not None
            and effective_threshold is not None
            and wind_speed_mph >= float(effective_threshold)
        ):
            return {
                **settings,
                "wind_speed_mph": wind_speed_mph,
                "wind_gust_mph": wind_gust_mph,
                "reason": (
                    f"Watering was stopped because the live wind speed reached "
                    f"{wind_speed_mph:.1f} mph, above the active limit of "
                    f"{float(effective_threshold):.1f} mph."
                ),
            }
        if (
            wind_gust_mph is not None
            and gust_threshold is not None
            and wind_gust_mph >= float(gust_threshold)
        ):
            return {
                **settings,
                "wind_speed_mph": wind_speed_mph,
                "wind_gust_mph": wind_gust_mph,
                "reason": (
                    f"Watering was stopped because the live wind gust reached "
                    f"{wind_gust_mph:.1f} mph, above the active gust limit of "
                    f"{float(gust_threshold):.1f} mph."
                ),
            }
        return None

    def _smooth_controller_plan(
        self,
        plan,
        previous_plan,
    ):
        """Apply light smoothing to published deficit values to avoid chart jitter."""

        target = self._normalize_deficit_value(plan.raw_deficit_inches)
        if previous_plan is None or previous_plan.decision != plan.decision:
            return replace(plan, deficit_inches=target)

        previous = previous_plan.deficit_inches
        delta = target - previous
        if abs(delta) <= DEFICIT_SMOOTHING_DEADBAND_INCHES:
            smoothed = previous
        elif abs(target - previous_plan.raw_deficit_inches) >= DEFICIT_SMOOTHING_BYPASS_DELTA_INCHES:
            smoothed = target
        else:
            smoothed = previous + (delta * DEFICIT_SMOOTHING_ALPHA)
            if abs(target) <= DEFICIT_SMOOTHING_NEAR_ZERO_INCHES and abs(smoothed) <= 0.015:
                smoothed = 0.0

        return replace(plan, deficit_inches=round(smoothed, 3))

    @staticmethod
    def _normalize_deficit_value(raw_deficit_inches: float) -> float:
        """Normalize tiny deficit noise before publishing to Home Assistant."""

        if abs(raw_deficit_inches) <= DEFICIT_SMOOTHING_NEAR_ZERO_INCHES:
            return 0.0
        return round(raw_deficit_inches, 2)

    def _resolve_planner_location(self) -> tuple[float, float, str]:
        """Return the latitude/longitude used by the planner."""

        configured_latitude = self._entry.options.get(CONF_PLANNER_LATITUDE)
        configured_longitude = self._entry.options.get(CONF_PLANNER_LONGITUDE)

        if configured_latitude not in (None, "") and configured_longitude not in (None, ""):
            try:
                latitude = float(configured_latitude)
                longitude = float(configured_longitude)
                if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
                    raise ValueError("Planner latitude/longitude override is out of range")
                return (
                    latitude,
                    longitude,
                    "integration_override",
                )
            except (TypeError, ValueError):
                _LOGGER.debug("Invalid planner latitude/longitude override", exc_info=True)
        elif configured_latitude not in (None, "") or configured_longitude not in (None, ""):
            _LOGGER.debug(
                "Ignoring partial planner location override because both latitude and longitude are required"
            )

        return (
            float(self.hass.config.latitude),
            float(self.hass.config.longitude),
            "home_assistant_location",
        )

    def get_controller_plan(self, device_id: str):
        """Return the current plan for a controller when available."""

        if self.data is None:
            return None
        for controller in self.data.controllers:
            if controller.device_id == device_id:
                return controller
        return None

    def get_zone_plan(self, device_id: str, zone_number: int):
        """Return the current plan for a zone when available."""

        controller = self.get_controller_plan(device_id)
        if controller is None:
            return None
        for zone in controller.zone_plans:
            if zone.zone_number == zone_number:
                return zone
        return None

    def _get_daily_rain_inputs(self) -> tuple[float | None, str]:
        """Return daily rain inches plus a configuration-status label."""

        entity_id = self._entry.options.get(CONF_DAILY_RAIN_ENTITY_ID)
        if not entity_id:
            return 0.0, "daily_rain_missing"

        state = self.hass.states.get(str(entity_id))
        if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            return None, "daily_rain_unavailable"

        try:
            return float(state.state), "configured"
        except (TypeError, ValueError):
            return None, "daily_rain_unavailable"

    def _normalize_daily_rain_rollover(
        self,
        raw_daily_rain_inches: float | None,
        *,
        today: date,
        previous_day_rain_inches: float | None,
        hour_of_day: int,
    ) -> float | None:
        """Normalize midnight rollover behavior for cumulative daily rain sensors.

        Some weather integrations expose a per-day cumulative rain total that briefly
        carries yesterday's ending value into the new day before resetting to zero.
        If we write that transient carry-over into today's ledger record, the 7-day
        total and deficits can spike until the source resets. Track a temporary
        rollover baseline so the new day only gets incremental rain after midnight.
        """

        if raw_daily_rain_inches is None:
            return None

        raw_daily_rain = round(max(0.0, float(raw_daily_rain_inches)), 3)
        tolerance = DAILY_RAIN_ROLLOVER_TOLERANCE_INCHES

        if self._last_daily_rain_source_date is not None and self._last_daily_rain_source_date != today:
            baseline_candidates = [
                value
                for value in (
                    self._last_daily_rain_source_value,
                    previous_day_rain_inches,
                )
                if value is not None
            ]
            baseline = max(baseline_candidates) if baseline_candidates else None
            if baseline is not None and raw_daily_rain >= (float(baseline) - tolerance):
                self._daily_rain_rollover_date = today
                self._daily_rain_rollover_baseline_inches = float(baseline)
            else:
                self._clear_daily_rain_rollover()
        elif (
            self._last_daily_rain_source_date is None
            and previous_day_rain_inches is not None
            and hour_of_day < DAILY_RAIN_ROLLOVER_GRACE_HOURS
            and raw_daily_rain > 0.0
            and raw_daily_rain >= (float(previous_day_rain_inches) - tolerance)
        ):
            self._daily_rain_rollover_date = today
            self._daily_rain_rollover_baseline_inches = float(previous_day_rain_inches)

        adjusted_daily_rain = raw_daily_rain
        if (
            self._daily_rain_rollover_date == today
            and self._daily_rain_rollover_baseline_inches is not None
        ):
            baseline = float(self._daily_rain_rollover_baseline_inches)
            if raw_daily_rain + tolerance < baseline:
                self._clear_daily_rain_rollover()
                adjusted_daily_rain = raw_daily_rain
            else:
                adjusted_daily_rain = round(max(0.0, raw_daily_rain - baseline), 3)

        self._last_daily_rain_source_date = today
        self._last_daily_rain_source_value = raw_daily_rain
        return adjusted_daily_rain

    def _clear_daily_rain_rollover(self) -> None:
        """Clear any in-progress daily rain rollover baseline."""

        self._daily_rain_rollover_date = None
        self._daily_rain_rollover_baseline_inches = None

    def _has_stable_balance_inputs(
        self,
        daily_rain_status: str,
        solar_radiation_status: str,
    ) -> bool:
        """Return True when configured weather inputs are available for a fresh ledger write."""

        if daily_rain_status == "daily_rain_unavailable" or solar_radiation_status != "configured":
            return False
        return not any(
            self._configured_numeric_input_unavailable(option_key)
            for option_key in (
                CONF_TEMPERATURE_ENTITY_ID,
                CONF_HUMIDITY_ENTITY_ID,
                CONF_WIND_SPEED_ENTITY_ID,
            )
        )

    def _configured_numeric_input_unavailable(self, option_key: str) -> bool:
        """Return True when a configured numeric input is temporarily unavailable."""

        entity_id = self._entry.options.get(option_key)
        if not entity_id:
            return False

        state = self.hass.states.get(str(entity_id))
        if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            return True

        try:
            float(state.state)
        except (TypeError, ValueError):
            return True
        return False

    def _load_daily_records(self, device_id: str) -> tuple[BhyveDailyWaterBalance, ...]:
        """Load stored daily balance records for a controller."""

        records = self._water_balance_store.get_daily_records(device_id)
        parsed: list[BhyveDailyWaterBalance] = []
        for record_date, values in records.items():
            parsed.append(
                BhyveDailyWaterBalance(
                    date=record_date,
                    raw_rain_inches=float(values.get("raw_rain_inches", 0.0)),
                    effective_rain_inches=float(values.get("effective_rain_inches", 0.0)),
                    et_inches=float(values.get("et_inches", 0.0)),
                )
            )
        return tuple(sorted(parsed, key=lambda item: item.date))

    @staticmethod
    def _get_daily_record(
        daily_records: tuple[BhyveDailyWaterBalance, ...],
        record_date: str,
    ) -> BhyveDailyWaterBalance | None:
        """Return a specific daily ledger record when it exists."""

        for record in daily_records:
            if record.date == record_date:
                return record
        return None

    def _get_numeric_option_state(self, option_key: str) -> float | None:
        """Return a selected HA entity state as a float when possible."""

        entity_id = self._entry.options.get(option_key)
        if not entity_id:
            return None

        state = self.hass.states.get(str(entity_id))
        if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            return None

        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _get_humidity_input(self) -> float | None:
        """Return relative humidity normalized to a 0-100 percentage scale."""

        entity_id = self._entry.options.get(CONF_HUMIDITY_ENTITY_ID)
        if not entity_id:
            return None

        state = self.hass.states.get(str(entity_id))
        if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            return None

        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None

        if value <= 1.0:
            value *= 100.0
        return max(0.0, min(100.0, value))

    def _get_wind_speed_input(self) -> float | None:
        """Return wind speed normalized to miles per hour when possible."""

        return self._get_wind_input(CONF_WIND_SPEED_ENTITY_ID)

    def _get_wind_gust_input(self) -> float | None:
        """Return wind gust normalized to miles per hour when possible."""

        return self._get_wind_input(CONF_WIND_GUST_ENTITY_ID)

    def _get_wind_input(self, option_key: str) -> float | None:
        """Return a wind-like sensor value normalized to mph when possible."""

        entity_id = self._entry.options.get(option_key)
        if not entity_id:
            return None

        state = self.hass.states.get(str(entity_id))
        if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            return None

        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None

        unit = str(state.attributes.get("unit_of_measurement") or "").strip().lower()
        compact_unit = unit.replace(" ", "")

        if compact_unit in {"mph", "mi/h", "mile/h", "miles/h", "milesperhour"}:
            wind_mph = value
        elif compact_unit in {"m/s", "meter/s", "meters/s", "metres/s", "metre/s"}:
            wind_mph = value * 2.23694
        elif compact_unit in {"km/h", "kph", "kmh", "kilometer/h", "kilometers/h"}:
            wind_mph = value / 1.60934
        elif compact_unit in {"ft/s", "fps"}:
            wind_mph = value * 0.681818
        elif compact_unit in {"kn", "kt", "knot", "knots"}:
            wind_mph = value * 1.15078
        elif compact_unit == "":
            wind_mph = value
        else:
            return None

        return max(0.0, round(wind_mph, 2))

    async def _async_get_forecast_inputs(self) -> tuple[float | None, float | None, str | None]:
        """Return the forecast amount/probability from native Home Assistant weather."""

        native_entity_id = self._entry.options.get(CONF_FORECAST_WEATHER_ENTITY_ID)
        if native_entity_id:
            native_forecast = await self._async_get_native_forecast(str(native_entity_id))
            if native_forecast is not None:
                amount, probability = native_forecast
                return amount, probability, f"weather:{native_entity_id}"
            return None, None, f"weather:{native_entity_id}"
        return None, None, None

    async def _async_get_native_forecast(
        self,
        weather_entity_id: str,
    ) -> tuple[float | None, float | None] | None:
        """Return the next daily rain amount and probability from a weather entity."""

        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"type": "daily", "entity_id": [weather_entity_id]},
                blocking=True,
                return_response=True,
            )
        except Exception:
            _LOGGER.debug(
                "Unable to query forecast data for %s via weather.get_forecasts",
                weather_entity_id,
                exc_info=True,
            )
            return None

        if not isinstance(response, dict):
            return None

        forecast_block = response.get(weather_entity_id)
        if not isinstance(forecast_block, dict):
            return None

        forecasts = forecast_block.get("forecast")
        if not isinstance(forecasts, list) or not forecasts:
            return None

        first = self._pick_forecast_entry(forecasts)
        if not isinstance(first, dict):
            return None

        amount_value = self._first_non_none(
            first,
            "precipitation",
            "native_precipitation",
            "precipitation_amount",
        )
        probability_value = self._first_non_none(
            first,
            "precipitation_probability",
            "probability_of_precipitation",
        )
        amount = self._coerce_float(amount_value)
        probability = self._coerce_float(probability_value)

        # Some forecast providers omit the precipitation amount field entirely
        # when no rain is expected. Treat a valid forecast entry with no amount
        # as 0.0 in rather than surfacing it as unavailable.
        if amount is None:
            amount = 0.0
        return amount, probability

    def _pick_forecast_entry(self, forecasts: list[Any]) -> dict[str, Any] | None:
        """Pick the most relevant next forecast entry from the weather response."""

        today = dt_util.now().date()
        future_with_values: list[dict[str, Any]] = []
        any_with_values: list[dict[str, Any]] = []
        for item in forecasts:
            if not isinstance(item, dict):
                continue

            has_forecast_values = any(
                item.get(key) is not None
                for key in (
                    "precipitation",
                    "native_precipitation",
                    "precipitation_amount",
                    "precipitation_probability",
                    "probability_of_precipitation",
                )
            )

            forecast_datetime = item.get("datetime") or item.get("native_datetime")
            if forecast_datetime is None:
                if has_forecast_values:
                    return item
                continue
            parsed = dt_util.parse_datetime(str(forecast_datetime))
            if parsed is None:
                if has_forecast_values:
                    return item
                continue
            if has_forecast_values:
                any_with_values.append(item)
            if parsed.date() > today and has_forecast_values:
                future_with_values.append(item)

        if future_with_values:
            return future_with_values[0]
        if any_with_values:
            return any_with_values[0]
        for item in forecasts:
            if isinstance(item, dict):
                return item
        return None

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        """Convert a weather-service value to float when possible."""

        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _first_non_none(item: dict[str, Any], *keys: str) -> Any:
        """Return the first present forecast value, preserving numeric zeroes."""

        for key in keys:
            value = item.get(key)
            if value is not None:
                return value
        return None
