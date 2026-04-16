"""The B-hyve Auto Sprinklers Controller integration."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
import logging

import voluptuous as vol
from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntryNotReady
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event, async_track_sunset
from homeassistant.util import dt as dt_util

from .api import BhyveApiError, BhyveAuthenticationError, async_login_and_get_client
from .const import (
    ATTR_DEVICE_ID,
    ATTR_DURATION,
    ATTR_ZONE_NUMBER,
    CONF_CONTROLLER_DEVICE_ID,
    CONF_FORECAST_RAIN_AMOUNT_ENTITY_ID,
    CONF_FORECAST_RAIN_PROBABILITY_ENTITY_ID,
    CONF_NOTIFICATION_SERVICE,
    CONF_WIND_GUST_ENTITY_ID,
    CONF_WIND_SPEED_ENTITY_ID,
    DEFAULT_AUTOMATIC_WATERING_ENABLED,
    DEFAULT_NOTIFICATIONS_ENABLED,
    DEFAULT_AUTOMATIC_WINDOW_ENABLED,
    DEFAULT_AUTOMATIC_WINDOW_PREFERENCE,
    DEFAULT_MAX_WATERING_WIND_SPEED_MPH,
    DEFAULT_MAX_AUTOMATIC_WATERING_WINDOW_MINUTES,
    DEFAULT_MINIMUM_RUN_THRESHOLD_MINUTES,
    DEFAULT_MIN_WATERING_TEMPERATURE_F,
    DEFAULT_OVERALL_WATERING_COEFFICIENT,
    DEFAULT_STARTUP_ENTITY_GRACE_PERIOD,
    DEFAULT_STARTUP_REFRESH_DELAY,
    DOMAIN,
    PLATFORMS,
    SERVICE_QUICK_RUN_ZONE,
    SERVICE_RECALCULATE_PLAN,
    SERVICE_REFRESH_ZONES,
    SERVICE_STOP_WATERING,
)
from .coordinator import BhyveIrrigationCoordinator
from .irrigation_api import BhyveIrrigationApi
from .ledger import BhyveWaterBalanceStore
from .models import BhyveRuntimeData, BhyveSprinklersConfigEntry
from .plan_coordinator import BhyveIrrigationPlanCoordinator
from .runtime_config import deserialize_runtime_config_snapshot

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration."""

    del config
    hass.data.setdefault(DOMAIN, {})
    await _async_register_services(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BhyveSprinklersConfigEntry,
) -> bool:
    """Set up B-hyve Auto Sprinklers Controller from a config entry."""

    _async_remove_legacy_forecast_config(entry, hass)
    _async_remove_legacy_forecast_entities(hass, entry)
    _async_remove_legacy_global_wind_profile_entity(hass, entry)
    _async_remove_legacy_base_runtime_entities(hass, entry)
    _async_remove_legacy_calibration_entities(hass, entry)

    try:
        client = await async_login_and_get_client(
            entry.data,
            async_get_clientsession(hass),
        )
    except BhyveAuthenticationError as err:
        raise ConfigEntryAuthFailed(
            "Unable to authenticate with B-hyve. Please reconfigure the integration."
        ) from err
    except (BhyveApiError, ClientError, OSError) as err:
        raise ConfigEntryNotReady(f"Unable to connect to B-hyve: {err}") from err

    irrigation_api = BhyveIrrigationApi(client)
    coordinator = BhyveIrrigationCoordinator(
        hass,
        irrigation_api,
        entry.data.get(CONF_CONTROLLER_DEVICE_ID) or None,
        entry.data["username"],
    )
    await coordinator.async_config_entry_first_refresh()

    water_balance_store = BhyveWaterBalanceStore(hass, entry.entry_id)
    await water_balance_store.async_load()
    stored_runtime_config_snapshot = water_balance_store.get_runtime_config_snapshot()
    runtime_config_snapshot = deserialize_runtime_config_snapshot(
        stored_runtime_config_snapshot
    )

    entry.runtime_data = BhyveRuntimeData(
        client=client,
        irrigation_api=irrigation_api,
        coordinator=coordinator,
        water_balance_store=water_balance_store,
        quick_run_durations={},
        zone_application_rates=runtime_config_snapshot["zone_application_rates"],
        zone_root_depths=runtime_config_snapshot["zone_root_depths"],
        zone_soil_whc=runtime_config_snapshot["zone_soil_whc"],
        zone_mad_values=runtime_config_snapshot["zone_mad_values"],
        zone_kc_values=runtime_config_snapshot["zone_kc_values"],
        zone_trigger_buffers=runtime_config_snapshot["zone_trigger_buffers"],
        max_weekly_run_times=runtime_config_snapshot["max_weekly_run_times"],
        zone_watering_coefficients=runtime_config_snapshot["zone_watering_coefficients"],
        zone_watering_profiles=runtime_config_snapshot["zone_watering_profiles"],
        zone_sprinkler_wind_profiles=runtime_config_snapshot["zone_sprinkler_wind_profiles"],
        controller_watering_day_restrictions=runtime_config_snapshot[
            "controller_watering_day_restrictions"
        ],
        zone_watering_day_restrictions={},
        watering_window_times=runtime_config_snapshot["watering_window_times"],
        automatic_window_preferences=(
            runtime_config_snapshot["automatic_window_preferences"]
            or {
                controller.device_id: DEFAULT_AUTOMATIC_WINDOW_PREFERENCE
                for controller in coordinator.data.controllers
            }
        ),
        automatic_window_max_minutes=(
            runtime_config_snapshot["automatic_window_max_minutes"]
            or {
                controller.device_id: DEFAULT_MAX_AUTOMATIC_WATERING_WINDOW_MINUTES
                for controller in coordinator.data.controllers
            }
        ),
        overall_watering_coefficient=runtime_config_snapshot[
            "overall_watering_coefficient"
        ],
        minimum_run_threshold_minutes=runtime_config_snapshot[
            "minimum_run_threshold_minutes"
        ],
        max_watering_wind_speed_mph=runtime_config_snapshot[
            "max_watering_wind_speed_mph"
        ],
        min_watering_temperature_f=runtime_config_snapshot[
            "min_watering_temperature_f"
        ],
        automatic_watering_enabled=runtime_config_snapshot[
            "automatic_watering_enabled"
        ],
        notifications_enabled=runtime_config_snapshot["notifications_enabled"],
        automatic_window_enabled=runtime_config_snapshot["automatic_window_enabled"],
        notification_service=(
            runtime_config_snapshot["notification_service"]
            or entry.options.get(CONF_NOTIFICATION_SERVICE)
            or None
        ),
        plan_coordinator=None,
        automatic_run_tokens={},
        sunset_calc_failed_date=None,
        last_sunset_notification_dates={},
    )

    plan_coordinator = BhyveIrrigationPlanCoordinator(
        hass,
        entry,
        water_balance_store,
    )
    entry.runtime_data.plan_coordinator = plan_coordinator
    entry.async_on_unload(plan_coordinator.async_clear_automatic_run_schedules)
    if stored_runtime_config_snapshot:
        await plan_coordinator.async_config_entry_first_refresh()
    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: hass.async_create_task(_async_handle_coordinator_update(entry))
        )
    )
    entry.async_on_unload(entry.add_update_listener(_async_handle_entry_update))
    _async_register_runtime_wind_listener(hass, entry)
    _async_register_sunset_plan_notification(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    hass.async_create_task(_async_startup_refresh(entry))
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: BhyveSprinklersConfigEntry,
) -> bool:
    """Unload a config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        await entry.runtime_data.client.async_close()

    return unload_ok


async def _async_handle_entry_update(
    hass: HomeAssistant,
    entry: BhyveSprinklersConfigEntry,
) -> None:
    """Reload the entry when config-entry options change."""

    await hass.config_entries.async_reload(entry.entry_id)


async def _async_startup_refresh(entry: BhyveSprinklersConfigEntry) -> None:
    """Run one extra refresh after setup so B-hyve-backed values populate immediately."""

    try:
        await asyncio.sleep(DEFAULT_STARTUP_REFRESH_DELAY.total_seconds())
        await entry.runtime_data.coordinator.async_request_refresh()
        await entry.runtime_data.plan_coordinator.async_request_refresh()
        grace_remaining = (
            DEFAULT_STARTUP_ENTITY_GRACE_PERIOD - DEFAULT_STARTUP_REFRESH_DELAY
        ).total_seconds()
        if grace_remaining > 0:
            await asyncio.sleep(grace_remaining)
        await entry.runtime_data.coordinator.async_request_refresh()
        await entry.runtime_data.plan_coordinator.async_resume_automatic_cycles()
        await entry.runtime_data.plan_coordinator.async_request_refresh()
    except Exception:
        _LOGGER.debug(
            "Unable to complete the startup B-hyve refresh for %s",
            entry.entry_id,
            exc_info=True,
        )


async def _async_handle_coordinator_update(entry: BhyveSprinklersConfigEntry) -> None:
    """Refresh derived planner state after B-hyve controller updates."""

    if entry.runtime_data.plan_coordinator is not None:
        await entry.runtime_data.plan_coordinator.async_request_refresh()


def _async_register_sunset_plan_notification(
    hass: HomeAssistant,
    entry: BhyveSprinklersConfigEntry,
) -> None:
    """Refresh the authoritative plan and notify immediately after sunset."""

    @callback
    def _async_handle_sunset(event_time) -> None:
        plan_coordinator = entry.runtime_data.plan_coordinator
        if plan_coordinator is None:
            return
        hass.async_create_task(
            plan_coordinator.async_refresh_for_sunset_notification(event_time)
        )

    entry.async_on_unload(
        async_track_sunset(hass, _async_handle_sunset, offset=timedelta(minutes=1))
    )


def _async_register_runtime_wind_listener(
    hass: HomeAssistant,
    entry: BhyveSprinklersConfigEntry,
) -> None:
    """Stop active watering if live wind conditions exceed the active threshold."""

    entity_ids = [
        str(entity_id)
        for entity_id in (
            entry.options.get(CONF_WIND_SPEED_ENTITY_ID),
            entry.options.get(CONF_WIND_GUST_ENTITY_ID),
        )
        if entity_id
    ]
    if not entity_ids:
        return

    stop_in_progress: set[str] = set()

    async def _async_handle_runtime_wind_change() -> None:
        coordinator = entry.runtime_data.coordinator
        plan_coordinator = entry.runtime_data.plan_coordinator
        if coordinator.data is None or plan_coordinator is None:
            return

        for controller in coordinator.data.controllers:
            active_run = controller.active_run
            if active_run is None:
                continue
            if controller.device_id in stop_in_progress:
                continue

            stop_reason = plan_coordinator.get_runtime_wind_stop_reason(
                controller.device_id,
                active_zone_number=active_run.zone_number,
            )
            if stop_reason is None:
                continue

            stop_in_progress.add(controller.device_id)
            try:
                await entry.runtime_data.water_balance_store.async_set_zone_weather_stop_hold(
                    controller.device_id,
                    active_run.zone_number,
                    date_key=dt_util.now().date().isoformat(),
                    reason=str(stop_reason["reason"]),
                    wind_speed_mph=(
                        float(stop_reason["wind_speed_mph"])
                        if stop_reason.get("wind_speed_mph") is not None
                        else None
                    ),
                    wind_gust_mph=(
                        float(stop_reason["wind_gust_mph"])
                        if stop_reason.get("wind_gust_mph") is not None
                        else None
                    ),
                    effective_wind_threshold_mph=float(
                        stop_reason["effective_max_watering_wind_speed_mph"]
                    ),
                    gust_threshold_mph=(
                        float(stop_reason["max_watering_gust_speed_mph"])
                        if stop_reason.get("max_watering_gust_speed_mph") is not None
                        else None
                    ),
                    effective_wind_profile=str(stop_reason["effective_wind_profile"]),
                    triggered_at=dt_util.now().isoformat(),
                )
                await plan_coordinator.async_cancel_automatic_cycle(controller.device_id)
                await coordinator.async_stop_watering(controller.device_id)
                await coordinator.async_request_refresh()
                await plan_coordinator.async_request_refresh()
            finally:
                stop_in_progress.discard(controller.device_id)

    @callback
    def _async_state_change_handler(_event) -> None:
        hass.async_create_task(_async_handle_runtime_wind_change())

    entry.async_on_unload(
        async_track_state_change_event(
            hass,
            entity_ids,
            _async_state_change_handler,
        )
    )


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register controller-level services."""

    for legacy_service in (
        "start_zone_calibration",
        "continue_zone_calibration",
        "save_zone_calibration",
        "cancel_zone_calibration",
    ):
        if hass.services.has_service(DOMAIN, legacy_service):
            hass.services.async_remove(DOMAIN, legacy_service)

    async def async_handle_quick_run(call: ServiceCall) -> None:
        """Handle the quick-run service."""

        coordinator = _async_find_coordinator(hass, call.data[ATTR_DEVICE_ID])
        await coordinator.async_quick_run_zone(
            call.data[ATTR_DEVICE_ID],
            int(call.data[ATTR_ZONE_NUMBER]),
            int(call.data[ATTR_DURATION]),
        )

    async def async_handle_stop(call: ServiceCall) -> None:
        """Handle the stop-watering service."""

        coordinator = _async_find_coordinator(hass, call.data[ATTR_DEVICE_ID])
        entry = _async_find_entry_for_coordinator(hass, coordinator)
        if entry.runtime_data.plan_coordinator is not None:
            await entry.runtime_data.plan_coordinator.async_cancel_automatic_cycle(
                call.data[ATTR_DEVICE_ID]
            )
        await coordinator.async_stop_watering(call.data[ATTR_DEVICE_ID])

    async def async_handle_refresh(call: ServiceCall) -> None:
        """Handle the refresh-zones service."""

        coordinator = _async_find_coordinator(hass, call.data[ATTR_DEVICE_ID])
        await coordinator.async_request_refresh()

    async def async_handle_recalculate(call: ServiceCall) -> None:
        """Handle the plan-recalculation service."""

        coordinator = _async_find_coordinator(hass, call.data[ATTR_DEVICE_ID])
        entry = _async_find_entry_for_coordinator(hass, coordinator)
        await coordinator.async_request_refresh()
        await entry.runtime_data.plan_coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, SERVICE_QUICK_RUN_ZONE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_QUICK_RUN_ZONE,
            async_handle_quick_run,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_DEVICE_ID): str,
                    vol.Required(ATTR_ZONE_NUMBER): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=1),
                    ),
                    vol.Required(ATTR_DURATION): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=1, max=7200),
                    ),
                }
            ),
        )
    if not hass.services.has_service(DOMAIN, SERVICE_STOP_WATERING):
        hass.services.async_register(
            DOMAIN,
            SERVICE_STOP_WATERING,
            async_handle_stop,
            schema=vol.Schema({vol.Required(ATTR_DEVICE_ID): str}),
        )
    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_ZONES):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH_ZONES,
            async_handle_refresh,
            schema=vol.Schema({vol.Required(ATTR_DEVICE_ID): str}),
        )
    if not hass.services.has_service(DOMAIN, SERVICE_RECALCULATE_PLAN):
        hass.services.async_register(
            DOMAIN,
            SERVICE_RECALCULATE_PLAN,
            async_handle_recalculate,
            schema=vol.Schema({vol.Required(ATTR_DEVICE_ID): str}),
        )


def _async_find_coordinator(
    hass: HomeAssistant,
    device_id: str,
) -> BhyveIrrigationCoordinator:
    """Find the coordinator that owns a sprinkler controller."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        config_entry = hass.config_entries.async_get_entry(entry.entry_id)
        if config_entry is None:
            continue

        typed_entry = config_entry
        with contextlib.suppress(AttributeError):
            coordinator = typed_entry.runtime_data.coordinator
            if coordinator.get_controller(device_id) is not None:
                return coordinator

    raise HomeAssistantError(f"No B-hyve sprinkler controller found for {device_id}")


def _async_find_entry_for_coordinator(
    hass: HomeAssistant,
    target_coordinator: BhyveIrrigationCoordinator,
) -> BhyveSprinklersConfigEntry:
    """Return the config entry that owns a coordinator."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        config_entry = hass.config_entries.async_get_entry(entry.entry_id)
        if config_entry is None:
            continue

        typed_entry = config_entry
        with contextlib.suppress(AttributeError):
            if typed_entry.runtime_data.coordinator is target_coordinator:
                return typed_entry

    raise HomeAssistantError("No B-hyve sprinkler config entry found for coordinator")


def _async_remove_legacy_forecast_config(
    entry: BhyveSprinklersConfigEntry,
    hass: HomeAssistant,
) -> None:
    """Drop old forecast fallback options that are no longer used."""

    legacy_keys = (
        CONF_FORECAST_RAIN_AMOUNT_ENTITY_ID,
        CONF_FORECAST_RAIN_PROBABILITY_ENTITY_ID,
    )
    if not any(key in entry.options for key in legacy_keys):
        return

    updated_options = dict(entry.options)
    for key in legacy_keys:
        updated_options.pop(key, None)
    hass.config_entries.async_update_entry(entry, options=updated_options)


def _async_remove_legacy_forecast_entities(
    hass: HomeAssistant,
    entry: BhyveSprinklersConfigEntry,
) -> None:
    """Remove old planner entities from the entity registry."""

    registry = er.async_get(hass)
    legacy_entities = (
        ("sensor", f"{entry.entry_id}_{CONF_FORECAST_RAIN_AMOUNT_ENTITY_ID}"),
        ("sensor", f"{entry.entry_id}_{CONF_FORECAST_RAIN_PROBABILITY_ENTITY_ID}"),
        ("sensor", f"{entry.entry_id}_forecast_rain_probability"),
        ("select", f"{entry.entry_id}_{CONF_FORECAST_RAIN_AMOUNT_ENTITY_ID}_source"),
        ("select", f"{entry.entry_id}_{CONF_FORECAST_RAIN_PROBABILITY_ENTITY_ID}_source"),
    )
    for domain, unique_id in legacy_entities:
        entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
        if entity_id is not None:
            registry.async_remove(entity_id)

    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = registry_entry.unique_id or ""
        if unique_id.endswith("_soil_moisture"):
            registry.async_remove(registry_entry.entity_id)


def _async_remove_legacy_global_wind_profile_entity(
    hass: HomeAssistant,
    entry: BhyveSprinklersConfigEntry,
) -> None:
    """Remove the old account-level wind profile selector from the registry."""

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "select",
        DOMAIN,
        f"{entry.entry_id}_sprinkler_wind_profile",
    )
    if entity_id is not None:
        registry.async_remove(entity_id)


def _async_remove_legacy_base_runtime_entities(
    hass: HomeAssistant,
    entry: BhyveSprinklersConfigEntry,
) -> None:
    """Remove legacy base-runtime entities now replaced by application-rate input."""

    registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = registry_entry.unique_id or ""
        if unique_id.endswith("_base_runtime"):
            registry.async_remove(registry_entry.entity_id)


def _async_remove_legacy_calibration_entities(
    hass: HomeAssistant,
    entry: BhyveSprinklersConfigEntry,
) -> None:
    """Remove old multi-step calibration entities from the registry."""

    registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = registry_entry.unique_id or ""
        if unique_id.endswith("_calibration_status") or unique_id.endswith(
            "_calibration_measured_depth"
        ):
            registry.async_remove(registry_entry.entity_id)
            continue
        if unique_id.startswith("Continue Calibration ") or unique_id.startswith(
            "Save Calibration "
        ):
            registry.async_remove(registry_entry.entity_id)
