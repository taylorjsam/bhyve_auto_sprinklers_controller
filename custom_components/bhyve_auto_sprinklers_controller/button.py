"""Button entities for B-hyve sprinkler controls."""

from __future__ import annotations

import json
import re
from pathlib import Path

from homeassistant.components import persistent_notification
from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CALIBRATION_RUN_DURATION_SECONDS,
    CONF_DAILY_RAIN_ENTITY_ID,
    CONF_ET_ENTITY_ID,
    CONF_FORECAST_WEATHER_ENTITY_ID,
    CONF_HUMIDITY_ENTITY_ID,
    CONF_IRRADIANCE_ENTITY_ID,
    CONF_TEMPERATURE_ENTITY_ID,
    CONF_UV_INDEX_ENTITY_ID,
    CONF_WIND_GUST_ENTITY_ID,
    CONF_WIND_SPEED_ENTITY_ID,
    DEFAULT_QUICK_RUN_DURATION,
    DOMAIN,
    WEEKDAY_KEYS,
    WEEKDAY_LABELS,
    normalize_zone_watering_profile,
    ZONE_WATERING_PROFILE_DISABLED,
    ZONE_WATERING_PROFILE_DROUGHT_TOLERANT,
    ZONE_WATERING_PROFILE_TREES_SHRUBS,
    ZONE_WATERING_PROFILE_VEGETABLE_GARDEN,
)
from .entity import (
    BhyveControllerPlanCoordinatorEntity,
    BhyveZoneCoordinatorEntity,
    BhyveZonePlanCoordinatorEntity,
)
from .models import (
    BhyveIrrigationSnapshot,
    BhyveSprinklerControllerSnapshot,
    BhyveSprinklersConfigEntry,
)
from .notifications import async_maybe_send_plan_notification


async def async_setup_entry(
    hass,
    entry: BhyveSprinklersConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up zone and controller buttons."""

    entities: list[ButtonEntity] = [BhyveExportDashboardTemplateButton(hass, entry)]
    for controller in entry.runtime_data.coordinator.data.controllers:
        entities.append(BhyveSprinklerExportDashboardButton(hass, entry, controller.device_id))
        entities.append(BhyveSprinklerRefreshButton(entry, controller.device_id))
        entities.append(BhyveSprinklerEvaluatePlanButton(entry, controller.device_id))
        entities.append(BhyveSprinklerWaterNowButton(entry, controller.device_id))
        entities.append(BhyveSprinklerStopAllButton(entry, controller.device_id))
        for zone in controller.zones:
            entities.append(
                BhyveSprinklerZoneWaterNowButton(
                    entry,
                    controller.device_id,
                    zone.zone_number,
                )
            )
            entities.append(
                BhyveSprinklerZoneCalibrateButton(
                    entry,
                    controller.device_id,
                    zone.zone_number,
                )
            )
            entities.append(
                BhyveSprinklerZoneButton(
                    entry,
                    controller.device_id,
                    zone.zone_number,
                )
            )

    async_add_entities(entities)


class BhyveExportDashboardTemplateButton(ButtonEntity):
    """Button entity that exports populated dashboards for all controllers."""

    _attr_has_entity_name = True
    _attr_name = "Export all dashboards"
    _attr_icon = "mdi:view-dashboard-edit-outline"

    def __init__(self, hass, entry: BhyveSprinklersConfigEntry) -> None:
        """Initialize the dashboard export button."""

        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_export_dashboard_template"

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the button to the account device."""

        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            manufacturer="Orbit B-hyve",
            model="Cloud Account",
            name="B-hyve Account",
        )

    async def async_press(self) -> None:
        """Write populated dashboard files for every discovered controller."""

        exported_dashboards: list[tuple[BhyveSprinklerControllerSnapshot, Path]] = []
        for controller in self._entry.runtime_data.coordinator.data.controllers:
            destination_path = await _async_export_controller_dashboard(
                self.hass,
                self._entry,
                controller,
            )
            exported_dashboards.append((controller, destination_path))

        persistent_notification.async_create(
            self.hass,
            (
                "Exported populated dashboard files for the discovered sprinkler controllers:\n\n"
                + "\n".join(
                    f"- `{_relative_dashboard_path(self.hass, path)}`"
                    for _, path in exported_dashboards
                )
                + "\n\n"
                "Home Assistant does not currently show a file-path field in the "
                "dashboard picker for YAML dashboards. Paste or merge this block into "
                "`configuration.yaml`, then restart Home Assistant:\n\n"
                f"```yaml\n{_dashboard_registration_block(self.hass, exported_dashboards)}\n```"
            ),
            title="B-hyve Auto Sprinklers Controller dashboards exported",
        )


class BhyveSprinklerExportDashboardButton(
    CoordinatorEntity[BhyveIrrigationSnapshot],
    ButtonEntity,
):
    """Button entity that exports a populated dashboard for one controller."""

    _attr_has_entity_name = True
    _attr_name = "Export dashboard"
    _attr_icon = "mdi:view-dashboard"

    def __init__(
        self,
        hass,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
    ) -> None:
        """Initialize the controller dashboard export button."""

        super().__init__(entry.runtime_data.coordinator)
        self.hass = hass
        self._entry = entry
        self._device_id = device_id
        self._attr_unique_id = f"Export Dashboard {device_id}"

    @property
    def available(self) -> bool:
        """Return True when the controller exists in coordinator data."""

        return super().available and self.controller is not None

    @property
    def controller(self) -> BhyveSprinklerControllerSnapshot | None:
        """Return the current controller snapshot."""

        for controller in self.coordinator.data.controllers:
            if controller.device_id == self._device_id:
                return controller
        return None

    @property
    def device_info(self) -> DeviceInfo:
        """Return controller device info."""

        controller = self.controller
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer="Orbit B-hyve",
            model=controller.product_model if controller else "Sprinkler Controller",
            name=controller.nickname if controller else "B-hyve Sprinkler Controller",
            via_device=(DOMAIN, self._entry.entry_id),
        )

    async def async_press(self) -> None:
        """Write a populated dashboard file for this controller."""

        controller = self.controller
        if controller is None:
            raise HomeAssistantError("Sprinkler controller is not available")

        destination_path = await _async_export_controller_dashboard(
            self.hass,
            self._entry,
            controller,
        )

        persistent_notification.async_create(
            self.hass,
            (
                f"Exported a ready-to-use dashboard for **{controller.nickname}** to "
                f"`{_relative_dashboard_path(self.hass, destination_path)}`.\n\n"
                "Home Assistant does not currently show a file-path field in the "
                "dashboard picker for YAML dashboards. Paste or merge this block into "
                "`configuration.yaml`, then restart Home Assistant:\n\n"
                f"```yaml\n{_dashboard_registration_snippet(self.hass, controller, destination_path)}\n```"
            ),
            title="B-hyve Auto Sprinklers Controller dashboard exported",
        )


def _write_dashboard_text(
    destination_dir: Path,
    destination_path: Path,
    dashboard_text: str,
) -> None:
    """Write a generated dashboard file into the HA config directory."""

    destination_dir.mkdir(parents=True, exist_ok=True)
    destination_path.write_text(dashboard_text, encoding="utf-8")


class BhyveSprinklerZoneButton(BhyveZoneCoordinatorEntity, ButtonEntity):
    """Button entity that starts a quick run for a zone."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_has_entity_name = True
    _attr_icon = "mdi:sprinkler"

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the zone quick-run button."""

        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"Start {device_id}-zone-{zone_number}"

    @property
    def name(self) -> str | None:
        """Return the entity name."""

        zone = self.zone
        if zone is None:
            return f"Zone {self._zone_number}"
        return zone.name

    @property
    def extra_state_attributes(self) -> dict[str, str | int | bool]:
        """Return helpful zone metadata."""

        zone = self.zone
        if zone is None:
            return {}

        return {
            "zone_number": zone.zone_number,
            "zone_id": zone.zone_id,
            "enabled": zone.enabled,
            "quickrun_duration": self._get_quick_run_duration(),
        }

    async def async_press(self) -> None:
        """Start a quick run using the zone's configured duration."""

        zone = self.zone
        if zone is None:
            raise HomeAssistantError("Zone is not available")
        if not zone.enabled:
            raise HomeAssistantError(f"Zone '{zone.name}' is disabled in B-hyve")

        await self.coordinator.async_quick_run_zone(
            self._device_id,
            self._zone_number,
            self._get_quick_run_duration(),
        )

    def _get_quick_run_duration(self) -> int:
        """Return the selected quick-run duration in seconds."""

        zone = self.zone
        if zone is not None and zone.quickrun_duration is not None:
            default_duration = zone.quickrun_duration
        elif zone is not None and zone.smart_duration is not None:
            default_duration = zone.smart_duration
        else:
            default_duration = DEFAULT_QUICK_RUN_DURATION

        return self._entry.runtime_data.quick_run_durations.get(
            self._duration_key,
            default_duration,
        )

    @property
    def _duration_key(self) -> str:
        """Return the shared runtime-data key for this zone."""

        return f"{self._device_id}:{self._zone_number}"


class BhyveSprinklerStopAllButton(
    CoordinatorEntity[BhyveIrrigationSnapshot],
    ButtonEntity,
):
    """Button entity that stops the currently running schedule."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_has_entity_name = True
    _attr_name = "Stop All Zones"
    _attr_icon = "mdi:octagon"

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
    ) -> None:
        """Initialize the stop-all button."""

        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._device_id = device_id
        self._attr_unique_id = f"Stop All {device_id}"

    @property
    def available(self) -> bool:
        """Return True when the controller exists in coordinator data."""

        return super().available and self.controller is not None

    @property
    def controller(self) -> BhyveSprinklerControllerSnapshot | None:
        """Return the current controller snapshot."""

        for controller in self.coordinator.data.controllers:
            if controller.device_id == self._device_id:
                return controller
        return None

    @property
    def device_info(self) -> DeviceInfo:
        """Return controller device info."""

        controller = self.controller
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer="Orbit B-hyve",
            model=controller.product_model if controller else "Sprinkler Controller",
            name=controller.nickname if controller else "B-hyve Sprinkler Controller",
            via_device=(DOMAIN, self._entry.entry_id),
        )

    async def async_press(self) -> None:
        """Stop watering on the controller."""

        if self.controller is None:
            raise HomeAssistantError("Sprinkler controller is not available")

        await self.coordinator.async_stop_watering(self._device_id)


class BhyveSprinklerZoneWaterNowButton(BhyveZonePlanCoordinatorEntity, ButtonEntity):
    """Button entity that recalculates and waters just this zone."""

    _attr_has_entity_name = True
    _attr_name = "Water now"
    _attr_icon = "mdi:play-circle"

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the zone water-now button."""

        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"Water Recommended {device_id}-zone-{zone_number}"

    async def async_press(self) -> None:
        """Recalculate and immediately water just this zone."""

        controller_plan = await _async_refresh_controller_plan(self._entry, self._device_id)
        zone_plan = None
        for candidate in controller_plan.zone_plans:
            if candidate.zone_number == self._zone_number:
                zone_plan = candidate
                break

        if zone_plan is None:
            raise HomeAssistantError("Zone plan is not available")
        if not zone_plan.enabled:
            raise HomeAssistantError(f"Zone '{zone_plan.zone_name}' is disabled in B-hyve")

        cycle_minutes = [segment for segment in zone_plan.cycle_minutes if segment > 0]
        if not cycle_minutes:
            raise HomeAssistantError(
                f"No watering is currently recommended for '{zone_plan.zone_name}'."
            )

        await self._entry.runtime_data.coordinator.async_run_zone_sequence(
            self._device_id,
            [
                (zone_plan.zone_number, int(segment * 60))
                for segment in cycle_minutes
            ],
            source="planned_zone",
        )


class BhyveSprinklerZoneCalibrateButton(BhyveZoneCoordinatorEntity, ButtonEntity):
    """Button entity that runs a simple tuna-can calibration cycle."""

    _attr_has_entity_name = True
    _attr_name = "Calibrate zone"
    _attr_icon = "mdi:cup-water"

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the zone-calibration run button."""

        super().__init__(entry, device_id, zone_number)
        self._attr_unique_id = f"Calibrate {device_id}-zone-{zone_number}"

    async def async_press(self) -> None:
        """Run this zone for 15 minutes so the user can measure application rate."""

        zone = self.zone
        if zone is None:
            raise HomeAssistantError("Zone is not available")
        if not zone.enabled:
            raise HomeAssistantError(f"Zone '{zone.name}' is disabled in B-hyve.")

        await self.coordinator.async_quick_run_zone(
            self._device_id,
            self._zone_number,
            CALIBRATION_RUN_DURATION_SECONDS,
            source="calibration",
            replace_existing=True,
        )


class BhyveSprinklerRefreshButton(
    CoordinatorEntity[BhyveIrrigationSnapshot],
    ButtonEntity,
):
    """Button entity that reloads controller values from B-hyve."""

    _attr_has_entity_name = True
    _attr_name = "Refresh B-hyve values"
    _attr_icon = "mdi:refresh"

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
    ) -> None:
        """Initialize the refresh button."""

        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._device_id = device_id
        self._attr_unique_id = f"Refresh {device_id}"

    @property
    def available(self) -> bool:
        """Return True when the controller exists in coordinator data."""

        return super().available and self.controller is not None

    @property
    def controller(self) -> BhyveSprinklerControllerSnapshot | None:
        """Return the current controller snapshot."""

        for controller in self.coordinator.data.controllers:
            if controller.device_id == self._device_id:
                return controller
        return None

    @property
    def device_info(self) -> DeviceInfo:
        """Return controller device info."""

        controller = self.controller
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer="Orbit B-hyve",
            model=controller.product_model if controller else "Sprinkler Controller",
            name=controller.nickname if controller else "B-hyve Sprinkler Controller",
            via_device=(DOMAIN, self._entry.entry_id),
        )

    async def async_press(self) -> None:
        """Reload controller state from B-hyve."""

        if self.controller is None:
            raise HomeAssistantError("Sprinkler controller is not available")

        await self.coordinator.async_request_refresh()


class BhyveSprinklerEvaluatePlanButton(
    CoordinatorEntity[BhyveIrrigationSnapshot],
    ButtonEntity,
):
    """Button entity that recalculates the irrigation plan."""

    _attr_has_entity_name = True
    _attr_name = "Evaluate irrigation plan"
    _attr_icon = "mdi:calculator-variant-outline"

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
    ) -> None:
        """Initialize the plan-evaluation button."""

        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._device_id = device_id
        self._attr_unique_id = f"Evaluate Plan {device_id}"

    @property
    def available(self) -> bool:
        """Return True when the controller exists in coordinator data."""

        return super().available and self.controller is not None

    @property
    def controller(self) -> BhyveSprinklerControllerSnapshot | None:
        """Return the current controller snapshot."""

        for controller in self.coordinator.data.controllers:
            if controller.device_id == self._device_id:
                return controller
        return None

    @property
    def device_info(self) -> DeviceInfo:
        """Return controller device info."""

        controller = self.controller
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer="Orbit B-hyve",
            model=controller.product_model if controller else "Sprinkler Controller",
            name=controller.nickname if controller else "B-hyve Sprinkler Controller",
            via_device=(DOMAIN, self._entry.entry_id),
        )

    async def async_press(self) -> None:
        """Recalculate irrigation recommendations for this controller."""

        if self.controller is None:
            raise HomeAssistantError("Sprinkler controller is not available")

        await _async_refresh_controller_plan(self._entry, self._device_id)
        await async_maybe_send_plan_notification(self._entry, self._device_id)


class BhyveSprinklerWaterNowButton(
    BhyveControllerPlanCoordinatorEntity,
    ButtonEntity,
):
    """Button entity that recalculates and waters all recommended zones now."""

    _attr_has_entity_name = True
    _attr_name = "Water recommended now"
    _attr_icon = "mdi:play-circle-outline"

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
    ) -> None:
        """Initialize the controller water-now button."""

        super().__init__(entry, device_id)
        self._attr_unique_id = f"Water Recommended {device_id}"

    async def async_press(self) -> None:
        """Recalculate irrigation recommendations and run them immediately."""

        controller_plan = await _async_refresh_controller_plan(self._entry, self._device_id)
        zone_runs: list[tuple[int, int]] = []
        for zone_plan in controller_plan.zone_plans:
            for segment in zone_plan.cycle_minutes:
                if segment > 0:
                    zone_runs.append((zone_plan.zone_number, int(segment * 60)))

        if not zone_runs:
            raise HomeAssistantError("No zones are currently recommended to run.")

        await self._entry.runtime_data.coordinator.async_run_zone_sequence(
            self._device_id,
            zone_runs,
            source="planned_cycle",
        )


async def _async_refresh_controller_plan(
    entry: BhyveSprinklersConfigEntry,
    device_id: str,
):
    """Refresh controller and plan data, then return the current controller plan."""

    await entry.runtime_data.coordinator.async_request_refresh()
    plan_coordinator = entry.runtime_data.plan_coordinator
    if plan_coordinator is None:
        raise HomeAssistantError("Irrigation planner is not available")

    await plan_coordinator.async_request_refresh()
    controller_plan = plan_coordinator.get_controller_plan(device_id)
    if controller_plan is None:
        raise HomeAssistantError("Sprinkler controller plan is not available")
    return controller_plan


async def _async_export_controller_dashboard(
    hass,
    entry: BhyveSprinklersConfigEntry,
    controller: BhyveSprinklerControllerSnapshot,
) -> Path:
    """Generate and write a populated dashboard for one controller."""

    account_entities, controller_entities = _async_dashboard_entity_maps(
        hass,
        entry,
        controller.device_id,
    )
    dashboard_text = _build_controller_dashboard_text(
        entry,
        controller,
        account_entities,
        controller_entities,
    )
    destination_dir = Path(hass.config.path("dashboards"))
    destination_path = destination_dir / _dashboard_filename(controller)
    await hass.async_add_executor_job(
        _write_dashboard_text,
        destination_dir,
        destination_path,
        dashboard_text,
    )
    return destination_path


def _async_dashboard_entity_maps(
    hass,
    entry: BhyveSprinklersConfigEntry,
    controller_device_id: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Return entity-id maps for the account and selected controller devices."""

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    account_device = device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    controller_device = device_registry.async_get_device(
        identifiers={(DOMAIN, controller_device_id)}
    )
    if controller_device is None:
        raise HomeAssistantError("Unable to locate the selected sprinkler controller device")

    account_entries = []
    if account_device is not None:
        account_entries = er.async_entries_for_device(entity_registry, account_device.id)
    controller_entries = er.async_entries_for_device(entity_registry, controller_device.id)
    return _registry_entity_map(account_entries), _registry_entity_map(controller_entries)


def _registry_entity_map(registry_entries) -> dict[str, str]:
    """Return entity IDs keyed by unique ID for enabled registry entries."""

    return {
        registry_entry.unique_id: registry_entry.entity_id
        for registry_entry in registry_entries
        if registry_entry.disabled_by is None and registry_entry.entity_id
    }


def _dashboard_filename(controller: BhyveSprinklerControllerSnapshot) -> str:
    """Return a stable exported dashboard filename for a controller."""

    slug = _slugify(controller.nickname or controller.device_id)
    suffix = controller.device_id[-6:].lower()
    return f"bhyve_auto_sprinklers_controller_{slug}_{suffix}.yaml"


def _dashboard_registration_key(controller: BhyveSprinklerControllerSnapshot) -> str:
    """Return a stable Lovelace dashboard key for a controller export."""

    slug = _slugify(controller.nickname or controller.device_id)
    suffix = controller.device_id[-6:].lower()
    return f"bhyve-auto-sprinklers-controller-{slug.replace('_', '-')}-{suffix}"


def _relative_dashboard_path(hass, destination_path: Path) -> str:
    """Return the dashboard path relative to the HA config directory."""

    config_root = Path(hass.config.path())
    try:
        return str(destination_path.relative_to(config_root))
    except ValueError:
        return str(destination_path)


def _dashboard_registration_block(
    hass,
    dashboards: list[tuple[BhyveSprinklerControllerSnapshot, Path]],
) -> str:
    """Return a complete Lovelace YAML registration block."""

    entries = "\n".join(
        _dashboard_registration_entry_snippet(hass, controller, destination_path)
        for controller, destination_path in dashboards
    )
    return "lovelace:\n  mode: storage\n  dashboards:\n" + entries


def _dashboard_registration_entry_snippet(
    hass,
    controller: BhyveSprinklerControllerSnapshot,
    destination_path: Path,
) -> str:
    """Return one dashboard entry for the Lovelace registration block."""

    return (
        "    "
        f"{_dashboard_registration_key(controller)}:\n"
        "      mode: yaml\n"
        f"      title: {json.dumps(controller.nickname or 'B-hyve Auto Sprinklers Controller')}\n"
        "      icon: mdi:sprinkler\n"
        "      show_in_sidebar: true\n"
        f"      filename: {json.dumps(_relative_dashboard_path(hass, destination_path))}"
    )


def _dashboard_registration_snippet(
    hass,
    controller: BhyveSprinklerControllerSnapshot,
    destination_path: Path,
) -> str:
    """Return the complete YAML snippet needed to register one dashboard."""

    return _dashboard_registration_block(hass, [(controller, destination_path)])


def _slugify(value: str) -> str:
    """Return a filesystem-safe slug."""

    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "sprinkler_controller"


def _build_controller_dashboard_text(
    entry: BhyveSprinklersConfigEntry,
    controller: BhyveSprinklerControllerSnapshot,
    account_entities: dict[str, str],
    controller_entities: dict[str, str],
) -> str:
    """Build a populated YAML dashboard for one controller."""

    lines: list[str] = []
    controller_name = controller.nickname or controller.device_id
    controller_slug = _slugify(controller_name)

    decision_entity = controller_entities.get(f"{controller.device_id}_irrigation_decision")
    deficit_entity = controller_entities.get(
        f"{controller.device_id}_average_zone_deficit_summary"
    )
    next_cycle_entity = controller_entities.get(f"{controller.device_id}_next_watering_cycle")
    last_watering_entity = controller_entities.get(f"{controller.device_id}_last_watering")
    suggested_start_entity = controller_entities.get(
        f"{controller.device_id}_suggested_start_watering_time"
    )
    suggested_end_entity = controller_entities.get(
        f"{controller.device_id}_suggested_end_watering_time"
    )
    effective_start_entity = controller_entities.get(
        f"{controller.device_id}_effective_start_watering_time"
    )
    effective_end_entity = controller_entities.get(
        f"{controller.device_id}_effective_end_watering_time"
    )
    api_status_entity = controller_entities.get(
        f"{entry.entry_id}_{controller.device_id}_api_status"
    )
    automatic_window_entity = controller_entities.get(
        f"{controller.device_id}_automatic_watering_window"
    )
    watering_start_time_entity = controller_entities.get(
        f"{controller.device_id}_watering_start_time"
    )
    watering_end_time_entity = controller_entities.get(
        f"{controller.device_id}_watering_end_time"
    )
    refresh_entity = controller_entities.get(f"Refresh {controller.device_id}")
    evaluate_entity = controller_entities.get(f"Evaluate Plan {controller.device_id}")
    water_now_entity = controller_entities.get(f"Water Recommended {controller.device_id}")
    stop_all_entity = controller_entities.get(f"Stop All {controller.device_id}")
    export_dashboard_entity = controller_entities.get(f"Export Dashboard {controller.device_id}")
    controller_watering_day_rows = [
        (
            controller_entities.get(f"{controller.device_id}_{weekday_key}_watering_day"),
            WEEKDAY_LABELS[weekday_key],
        )
        for weekday_key in WEEKDAY_KEYS
    ]

    automatic_watering_entity = account_entities.get(f"{entry.entry_id}_automatic_watering")
    notifications_entity = account_entities.get(f"{entry.entry_id}_notifications_enabled")
    notification_target_entity = account_entities.get(f"{entry.entry_id}_notification_target")
    coefficient_entity = account_entities.get(
        f"{entry.entry_id}_overall_watering_coefficient"
    )
    max_automatic_window_entity = controller_entities.get(
        f"{controller.device_id}_maximum_automatic_watering_window"
    )
    automatic_window_preference_entity = controller_entities.get(
        f"{controller.device_id}_automatic_window_preference"
    )
    minimum_run_threshold_entity = account_entities.get(
        f"{entry.entry_id}_minimum_run_threshold"
    )
    max_wind_speed_entity = account_entities.get(
        f"{entry.entry_id}_max_watering_wind_speed"
    )
    minimum_temperature_entity = account_entities.get(
        f"{entry.entry_id}_minimum_watering_temperature"
    )
    daily_rain_sensor = account_entities.get(f"{entry.entry_id}_{CONF_DAILY_RAIN_ENTITY_ID}")
    effective_rain_sensor = account_entities.get(f"{entry.entry_id}_effective_rain_24h")
    weekly_rain_sensor = account_entities.get(f"{entry.entry_id}_weekly_rain_computed")
    forecast_rain_sensor = account_entities.get(f"{entry.entry_id}_forecast_rain_next_24h")
    temperature_sensor = account_entities.get(
        f"{entry.entry_id}_{CONF_TEMPERATURE_ENTITY_ID}"
    )
    humidity_sensor = account_entities.get(f"{entry.entry_id}_{CONF_HUMIDITY_ENTITY_ID}")
    solar_radiation_sensor = account_entities.get(
        f"{entry.entry_id}_{CONF_IRRADIANCE_ENTITY_ID}"
    )
    uv_sensor = account_entities.get(f"{entry.entry_id}_{CONF_UV_INDEX_ENTITY_ID}")
    wind_speed_sensor = account_entities.get(f"{entry.entry_id}_{CONF_WIND_SPEED_ENTITY_ID}")
    wind_gust_sensor = account_entities.get(f"{entry.entry_id}_{CONF_WIND_GUST_ENTITY_ID}")
    et_sensor = account_entities.get(f"{entry.entry_id}_{CONF_ET_ENTITY_ID}")
    daily_rain_source_select = account_entities.get(
        f"{entry.entry_id}_{CONF_DAILY_RAIN_ENTITY_ID}_source"
    )
    forecast_weather_source_select = account_entities.get(
        f"{entry.entry_id}_{CONF_FORECAST_WEATHER_ENTITY_ID}_source"
    )
    temperature_source_select = account_entities.get(
        f"{entry.entry_id}_{CONF_TEMPERATURE_ENTITY_ID}_source"
    )
    humidity_source_select = account_entities.get(
        f"{entry.entry_id}_{CONF_HUMIDITY_ENTITY_ID}_source"
    )
    solar_radiation_source_select = account_entities.get(
        f"{entry.entry_id}_{CONF_IRRADIANCE_ENTITY_ID}_source"
    )
    uv_source_select = account_entities.get(f"{entry.entry_id}_{CONF_UV_INDEX_ENTITY_ID}_source")
    wind_speed_source_select = account_entities.get(
        f"{entry.entry_id}_{CONF_WIND_SPEED_ENTITY_ID}_source"
    )
    wind_gust_source_select = account_entities.get(
        f"{entry.entry_id}_{CONF_WIND_GUST_ENTITY_ID}_source"
    )
    zones = sorted(controller.zones, key=lambda item: item.zone_number)
    zone_dashboard_data: list[dict[str, str | int | None]] = []
    for zone in zones:
        valve_entity = controller_entities.get(f"{controller.device_id}_{zone.zone_number}_valve")
        recommended_runtime_entity = controller_entities.get(
            f"{controller.device_id}_{zone.zone_number}_recommended_runtime"
        )
        overview_runtime_entity = controller_entities.get(
            f"{controller.device_id}_{zone.zone_number}_overview_runtime"
        )
        zone_deficit_entity = controller_entities.get(
            f"{controller.device_id}_{zone.zone_number}_zone_deficit"
        )
        application_rate_entity = controller_entities.get(
            f"{controller.device_id}_{zone.zone_number}_application_rate"
        )
        runtime_this_week_entity = controller_entities.get(
            f"{controller.device_id}_{zone.zone_number}_runtime_this_week"
        )
        capacity_entity = controller_entities.get(
            f"{controller.device_id}_{zone.zone_number}_capacity"
        )
        watering_coefficient_entity = controller_entities.get(
            f"{controller.device_id}_{zone.zone_number}_watering_coefficient"
        )
        max_weekly_runtime_entity = controller_entities.get(
            f"{controller.device_id}_{zone.zone_number}_max_weekly_run_time"
        )
        max_weekly_runtime_status_entity = controller_entities.get(
            f"{controller.device_id}_{zone.zone_number}_weekly_cap_status"
        )
        profile_entity = controller_entities.get(
            f"{controller.device_id}_{zone.zone_number}_watering_profile"
        )
        sprinkler_head_type_entity = controller_entities.get(
            f"{controller.device_id}_{zone.zone_number}_sprinkler_wind_profile"
        )
        root_depth_entity = controller_entities.get(
            f"{controller.device_id}_{zone.zone_number}_root_depth"
        )
        soil_whc_entity = controller_entities.get(
            f"{controller.device_id}_{zone.zone_number}_soil_whc"
        )
        mad_entity = controller_entities.get(
            f"{controller.device_id}_{zone.zone_number}_mad"
        )
        kc_entity = controller_entities.get(
            f"{controller.device_id}_{zone.zone_number}_kc"
        )
        trigger_buffer_entity = controller_entities.get(
            f"{controller.device_id}_{zone.zone_number}_trigger_buffer"
        )
        settings_entity = controller_entities.get(
            f"{controller.device_id}_{zone.zone_number}_settings"
        )
        water_now_zone_entity = controller_entities.get(
            f"Water Recommended {controller.device_id}-zone-{zone.zone_number}"
        )
        calibrate_zone_entity = controller_entities.get(
            f"Calibrate {controller.device_id}-zone-{zone.zone_number}"
        )
        zone_dashboard_data.append(
            {
                "name": zone.name,
                "label": _compact_zone_name(zone.name, controller.nickname),
                "zone_number": zone.zone_number,
                "runtime_key": f"{controller.device_id}:{zone.zone_number}",
                "valve": valve_entity,
                "recommended_runtime": recommended_runtime_entity,
                "overview_runtime": overview_runtime_entity,
                "deficit": zone_deficit_entity,
                "application_rate": application_rate_entity,
                "capacity": capacity_entity,
                "runtime_this_week": runtime_this_week_entity,
                "watering_coefficient": watering_coefficient_entity,
                "max_weekly_runtime": max_weekly_runtime_entity,
                "max_weekly_runtime_status": max_weekly_runtime_status_entity,
                "profile": profile_entity,
                "sprinkler_head_type": sprinkler_head_type_entity,
                "root_depth": root_depth_entity,
                "soil_whc": soil_whc_entity,
                "mad": mad_entity,
                "kc": kc_entity,
                "trigger_buffer": trigger_buffer_entity,
                "calibrate": calibrate_zone_entity,
                "settings": settings_entity,
                "water_now": water_now_zone_entity,
            }
        )

    lines.append(f"title: {_yaml_quote(f'B-hyve Auto Sprinklers Controller - {controller_name}')}")
    lines.append("views:")

    lines.append("  - title: \"Overview\"")
    lines.append(f"    path: {_yaml_quote(controller_slug)}")
    lines.append("    icon: mdi:sprinkler-variant")
    lines.append("    type: masonry")
    lines.append("    cards:")
    lines.append("      - type: vertical-stack")
    lines.append("        cards:")
    _append_markdown_card(
        lines,
        _overview_header_markdown_lines(controller_name),
        indent="          ",
        variant="hero",
    )
    _append_tile_grid(
        lines,
        [
            (api_status_entity, "API"),
            (decision_entity, "Plan"),
            (deficit_entity, "Avg deficit"),
            (forecast_rain_sensor, "Forecast 24h"),
            (last_watering_entity, "Last watering"),
        ],
        columns=2,
        indent="          ",
        variant="status",
    )
    _append_markdown_card(
        lines,
        _section_markdown_lines("Weather Inputs", ""),
        indent="          ",
        variant="section",
    )
    _append_tile_grid(
        lines,
        [
            (daily_rain_sensor, "Daily rain"),
            (effective_rain_sensor, "Effective 24h"),
            (weekly_rain_sensor, "Rain 7d"),
            (forecast_rain_sensor, "Forecast 24h"),
            (et_sensor, "Hourly ET"),
            (solar_radiation_sensor, "Solar radiation"),
            (temperature_sensor, "Temperature"),
            (humidity_sensor, "Humidity"),
            (wind_speed_sensor, "Wind"),
            (wind_gust_sensor, "Gust"),
            (uv_sensor, "UV index"),
        ],
        columns=2,
        indent="          ",
        variant="metric",
    )
    _append_markdown_card(
        lines,
        _section_markdown_lines("Watering Window", ""),
        indent="          ",
        variant="section",
    )
    _append_markdown_card(
        lines,
        _watering_window_markdown_lines(
            effective_start_entity=effective_start_entity,
            effective_end_entity=effective_end_entity,
            suggested_start_entity=suggested_start_entity,
            suggested_end_entity=suggested_end_entity,
        ),
        indent="          ",
        variant="note",
    )

    lines.append("      - type: vertical-stack")
    lines.append("        cards:")
    if next_cycle_entity is not None:
        _append_markdown_card(
            lines,
            _projected_cycle_markdown_lines(next_cycle_entity, zone_dashboard_data),
            indent="          ",
            variant="note",
        )
    _append_button_grid(
        lines,
        [
            (water_now_entity, "Water now"),
            (refresh_entity, "Refresh"),
            (evaluate_entity, "Evaluate"),
            (stop_all_entity, "Stop all"),
        ],
        columns=2,
        indent="          ",
        variant="action",
    )
    _append_markdown_card(
        lines,
        _weekly_runtime_markdown_lines(zone_dashboard_data),
        indent="          ",
        variant="note",
    )

    lines.append("      - type: vertical-stack")
    lines.append("        cards:")
    _append_markdown_card(
        lines,
        _section_markdown_lines("Zones", ""),
        indent="          ",
        variant="section",
    )
    _append_entities_card(
        lines,
        "",
        [
            zone_data["overview_runtime"] or zone_data["recommended_runtime"]
            for zone_data in zone_dashboard_data
        ],
        indent="          ",
        variant="zones",
    )
    _append_markdown_card(
        lines,
        _section_markdown_lines("Automation", ""),
        indent="          ",
        variant="section",
    )
    _append_tile_grid(
        lines,
        [
            (automatic_watering_entity, "Automatic watering"),
            (notifications_entity, "Notifications"),
            (automatic_window_entity, "Auto watering window"),
        ],
        columns=1,
        indent="          ",
        variant="toggle",
    )
    _append_tile_grid(
        lines,
        [
            (coefficient_entity, "Overall multiplier"),
            (minimum_run_threshold_entity, "Minimum runtime"),
            (max_wind_speed_entity, "Max wind"),
            (minimum_temperature_entity, "Min temperature"),
        ],
        columns=2,
        indent="          ",
        variant="guardrail",
    )
    lines.append("  - title: \"Zones\"")
    lines.append(f"    path: {_yaml_quote(f'{controller_slug}_zones')}")
    lines.append("    icon: mdi:sprinkler")
    lines.append("    type: masonry")
    lines.append("    cards:")
    for zone_data in zone_dashboard_data:
        lines.append("      - type: vertical-stack")
        lines.append("        cards:")
        _append_markdown_card(
            lines,
            _zone_card_header_markdown_lines(
                str(zone_data["name"]),
                int(zone_data["zone_number"]),
            ),
            indent="          ",
            variant="zone-header",
        )
        _append_zone_detail_grid(
            lines,
            zone_data,
            indent="          ",
        )
        _append_history_graph(
            lines,
            "Deficit - Last 7 Days",
            [
                (zone_data["deficit"], "Deficit"),
            ],
            hours_to_show=168,
            indent="          ",
            variant="chart",
        )

    lines.append("  - title: \"Settings\"")
    lines.append(f"    path: {_yaml_quote(f'{controller_slug}_settings')}")
    lines.append("    icon: mdi:tune-variant")
    lines.append("    type: masonry")
    lines.append("    cards:")
    lines.append("      - type: vertical-stack")
    lines.append("        cards:")
    _append_markdown_card(
        lines,
        _section_markdown_lines(
            "Planner Controls",
            "",
        ),
        indent="          ",
        variant="section",
    )
    _append_entities_card(
        lines,
        "",
        [
            (automatic_watering_entity, "Automatic watering"),
            (notifications_entity, "Notifications"),
            (notification_target_entity, "Notification target"),
            (coefficient_entity, "Overall multiplier"),
            (minimum_run_threshold_entity, "Minimum runtime"),
            (automatic_window_entity, "Use auto window"),
            (max_automatic_window_entity, "Max auto window"),
            (automatic_window_preference_entity, "Auto window timing"),
            (max_wind_speed_entity, "Max wind"),
            (minimum_temperature_entity, "Min temperature"),
            (watering_start_time_entity, "Manual start"),
            (watering_end_time_entity, "Manual end"),
        ],
        indent="          ",
        variant="entities",
    )
    _append_markdown_card(
        lines,
        _section_markdown_lines(
            "Weather Sources",
            "",
        ),
        indent="          ",
        variant="section",
    )
    _append_entities_card(
        lines,
        "",
        [
            (daily_rain_source_select, "Daily rain source"),
            (forecast_weather_source_select, "Forecast weather source"),
            (solar_radiation_source_select, "Solar radiation source"),
            (temperature_source_select, "Temperature source"),
            (humidity_source_select, "Humidity source"),
            (uv_source_select, "UV source"),
            (wind_speed_source_select, "Wind source"),
            (wind_gust_source_select, "Gust source"),
        ],
        indent="          ",
        variant="entities",
    )
    _append_tile_grid(
        lines,
        [
            (daily_rain_sensor, "Daily rain"),
            (weekly_rain_sensor, "Rain 7d"),
            (effective_rain_sensor, "Effective 24h"),
            (forecast_rain_sensor, "Forecast 24h"),
            (et_sensor, "Hourly ET"),
            (solar_radiation_sensor, "Solar radiation"),
            (temperature_sensor, "Temperature"),
            (humidity_sensor, "Humidity"),
            (wind_speed_sensor, "Wind"),
            (wind_gust_sensor, "Gust"),
            (uv_sensor, "UV index"),
        ],
        columns=2,
        indent="          ",
        variant="source",
    )
    _append_markdown_card(
        lines,
        _section_markdown_lines(
            "Allowed watering days",
            "",
        ),
        indent="          ",
        variant="section",
    )
    _append_entities_card(
        lines,
        "",
        controller_watering_day_rows,
        indent="          ",
        variant="entities",
    )
    _append_markdown_card(
        lines,
        _section_markdown_lines(
            "Zone application rates",
            "",
        ),
        indent="          ",
        variant="section",
    )
    _append_entities_card(
        lines,
        "",
        [
            (zone_data["application_rate"], f"{zone_data['label']} application rate")
            for zone_data in zone_dashboard_data
        ],
        indent="          ",
        variant="entities",
    )
    _append_markdown_card(
        lines,
        _section_markdown_lines(
            "Zone weekly caps",
            "",
        ),
        indent="          ",
        variant="section",
    )
    _append_entities_card(
        lines,
        "",
        [
            (zone_data["max_weekly_runtime"], f"{zone_data['label']} weekly cap")
            for zone_data in zone_dashboard_data
        ],
        indent="          ",
        variant="entities",
    )
    lines.append("      - type: vertical-stack")
    lines.append("        cards:")
    _append_markdown_card(
        lines,
        _section_markdown_lines(
            "Recalibrate zones",
            "",
        ),
        indent="          ",
        variant="section",
    )
    _append_button_grid(
        lines,
        [
            (zone_data["calibrate"], f"Calibrate {zone_data['label']}")
            for zone_data in zone_dashboard_data
        ],
        columns=2,
        indent="          ",
        variant="action",
    )
    _append_button_grid(
        lines,
        [
            (refresh_entity, "Refresh"),
            (export_dashboard_entity, "Re-export"),
        ],
        columns=2,
        indent="          ",
        variant="action",
    )
    _append_markdown_card(
        lines,
        _section_markdown_lines(
            "Zone agronomy",
            "",
        ),
        indent="          ",
        variant="section",
    )
    _append_entities_card(
        lines,
        "",
        [
            (zone_data["root_depth"], f"{zone_data['label']} root depth")
            for zone_data in zone_dashboard_data
        ]
        + [
            (zone_data["soil_whc"], f"{zone_data['label']} soil WHC")
            for zone_data in zone_dashboard_data
        ]
        + [
            (zone_data["mad"], f"{zone_data['label']} MAD")
            for zone_data in zone_dashboard_data
        ]
        + [
            (zone_data["kc"], f"{zone_data['label']} kc")
            for zone_data in zone_dashboard_data
        ]
        + [
            (zone_data["trigger_buffer"], f"{zone_data['label']} trigger buffer")
            for zone_data in zone_dashboard_data
        ]
        + [
            (zone_data["capacity"], f"{zone_data['label']} capacity")
            for zone_data in zone_dashboard_data
        ],
        indent="          ",
        variant="entities",
    )

    return "\n".join(lines) + "\n"


def _append_markdown_card(
    lines: list[str],
    content_lines: list[str],
    *,
    indent: str = "      ",
    variant: str = "default",
) -> None:
    """Append a markdown card."""

    lines.append(f"{indent}- type: markdown")
    lines.append(f"{indent}  content: |")
    for content_line in content_lines:
        lines.append(f"{indent}    {content_line}")
    _append_card_mod(lines, _markdown_card_style_lines(variant), indent=indent)


def _append_tile_grid(
    lines: list[str],
    cards: list[tuple[str | None, str]],
    *,
    columns: int = 4,
    indent: str = "      ",
    variant: str = "default",
) -> None:
    """Append a tile grid card when at least one entity is available."""

    valid_cards = [(entity_id, name) for entity_id, name in cards if entity_id is not None]
    if not valid_cards:
        return

    lines.append(f"{indent}- type: grid")
    lines.append(f"{indent}  columns: {columns}")
    lines.append(f"{indent}  square: false")
    _append_card_mod(lines, _grid_card_style_lines(variant), indent=indent)
    lines.append(f"{indent}  cards:")
    for entity_id, name in valid_cards:
        lines.append(f"{indent}    - type: tile")
        lines.append(f"{indent}      entity: {entity_id}")
        lines.append(f"{indent}      name: {_yaml_quote(name)}")
        lines.append(f"{indent}      vertical: false")
        _append_card_mod(
            lines,
            _tile_card_style_lines(variant, name),
            indent=f"{indent}    ",
        )


def _append_button_grid(
    lines: list[str],
    cards: list[tuple[str | None, str]],
    *,
    columns: int = 2,
    indent: str = "      ",
    variant: str = "default",
) -> None:
    """Append a grid of button cards."""

    valid_cards = [(entity_id, name) for entity_id, name in cards if entity_id is not None]
    if not valid_cards:
        return

    lines.append(f"{indent}- type: grid")
    lines.append(f"{indent}  columns: {columns}")
    lines.append(f"{indent}  square: false")
    _append_card_mod(lines, _grid_card_style_lines(f"{variant}_buttons"), indent=indent)
    lines.append(f"{indent}  cards:")
    for entity_id, name in valid_cards:
        lines.append(f"{indent}    - type: button")
        lines.append(f"{indent}      entity: {entity_id}")
        lines.append(f"{indent}      name: {_yaml_quote(name)}")
        lines.append(f"{indent}      show_state: false")
        lines.append(f"{indent}      show_icon: true")
        lines.append(f"{indent}      show_name: true")
        lines.append(f"{indent}      tap_action:")
        lines.append(f"{indent}        action: call-service")
        lines.append(f"{indent}        service: button.press")
        lines.append(f"{indent}        target:")
        lines.append(f"{indent}          entity_id: {entity_id}")
        confirmation_text = _button_confirmation_text(name, entity_id)
        if confirmation_text:
            lines.append(f"{indent}        confirmation:")
            lines.append(f"{indent}          text: {_yaml_quote(confirmation_text)}")
        lines.append(f"{indent}      hold_action:")
        lines.append(f"{indent}        action: none")
        lines.append(f"{indent}      double_tap_action:")
        lines.append(f"{indent}        action: none")
        _append_card_mod(
            lines,
            _button_card_style_lines(variant, name),
            indent=f"{indent}    ",
        )


def _button_confirmation_text(name: str, entity_id: str | None = None) -> str | None:
    """Return an optional confirmation prompt for direct-action dashboard buttons."""

    if "Stop" in name:
        return "Stop all active sprinkler zones?"
    target_text = f"{name} {entity_id or ''}".lower()
    if "calibrate" in target_text:
        return _calibration_confirmation_text()
    return None


def _calibration_confirmation_text() -> str:
    """Return the dashboard prompt for a simple tuna-can calibration run."""

    return (
        "Place a tuna can or similar straight-sided container in this zone. "
        "After this 15-minute run finishes, measure the water depth in inches, "
        "multiply that value by 4, and enter the result as this zone's "
        "Application rate (in/hr) in Settings. Start the 15-minute calibration run now?"
    )


def _append_logbook_card(
    lines: list[str],
    title: str,
    entity_ids: list[str],
    *,
    hours_to_show: int = 168,
    indent: str = "      ",
) -> None:
    """Append a native logbook card for recent activity."""

    if not entity_ids:
        return

    lines.append(f"{indent}- type: logbook")
    lines.append(f"{indent}  title: {_yaml_quote(title)}")
    lines.append(f"{indent}  hours_to_show: {hours_to_show}")
    lines.append(f"{indent}  entities:")
    for entity_id in entity_ids:
        lines.append(f"{indent}    - {entity_id}")
    _append_card_mod(lines, _logbook_card_style_lines(), indent=indent)


def _append_zone_detail_grid(
    lines: list[str],
    zone_data: dict[str, str | int | None],
    *,
    indent: str = "      ",
) -> None:
    """Append a mixed zone-detail grid."""

    specs = [
        ("button", zone_data["water_now"], "Water zone now"),
        ("button", zone_data["calibrate"], "Calibrate zone"),
        ("tile", zone_data["valve"], "Valve"),
        ("tile", zone_data["application_rate"], "Application rate"),
        ("tile", zone_data["recommended_runtime"], "Recommended"),
        ("tile", zone_data["deficit"], "Deficit"),
        ("tile", zone_data["profile"], "Profile"),
        ("tile", zone_data["sprinkler_head_type"], "Sprinkler head type"),
        ("tile", zone_data["watering_coefficient"], "Zone multiplier"),
        ("tile", zone_data["capacity"], "Capacity"),
        ("tile", zone_data["max_weekly_runtime_status"], "Weekly cap"),
        ("tile", zone_data["runtime_this_week"], "This week"),
    ]
    valid_specs = [(kind, entity_id, name) for kind, entity_id, name in specs if entity_id]

    if not valid_specs:
        return

    lines.append(f"{indent}- type: grid")
    lines.append(f"{indent}  columns: 2")
    lines.append(f"{indent}  square: false")
    _append_card_mod(lines, _grid_card_style_lines("zone_details"), indent=indent)
    lines.append(f"{indent}  cards:")
    for kind, entity_id, name in valid_specs:
        if kind == "button":
            lines.append(f"{indent}    - type: button")
            lines.append(f"{indent}      entity: {entity_id}")
            lines.append(f"{indent}      name: {_yaml_quote(name)}")
            lines.append(f"{indent}      show_state: false")
            lines.append(f"{indent}      show_icon: true")
            lines.append(f"{indent}      show_name: true")
            lines.append(f"{indent}      tap_action:")
            lines.append(f"{indent}        action: call-service")
            lines.append(f"{indent}        service: button.press")
            lines.append(f"{indent}        target:")
            lines.append(f"{indent}          entity_id: {entity_id}")
            confirmation_text = _button_confirmation_text(name, str(entity_id))
            if confirmation_text:
                lines.append(f"{indent}        confirmation:")
                lines.append(f"{indent}          text: {_yaml_quote(confirmation_text)}")
            lines.append(f"{indent}      hold_action:")
            lines.append(f"{indent}        action: none")
            lines.append(f"{indent}      double_tap_action:")
            lines.append(f"{indent}        action: none")
            _append_card_mod(
                lines,
                _button_card_style_lines("zone_action", name),
                indent=f"{indent}    ",
            )
            continue

        lines.append(f"{indent}    - type: {kind}")
        lines.append(f"{indent}      entity: {entity_id}")
        lines.append(f"{indent}      name: {_yaml_quote(name)}")
        lines.append(f"{indent}      vertical: false")
        _append_card_mod(
            lines,
            _tile_card_style_lines("zone", name),
            indent=f"{indent}    ",
        )


def _append_entities_card(
    lines: list[str],
    title: str,
    entity_rows: list[str | tuple[str | None, str] | dict[str, str | None]],
    *,
    indent: str = "      ",
    variant: str = "default",
) -> None:
    """Append an entities card for the provided entity rows."""

    valid_entities = [
        row
        for row in entity_rows
        if (
            isinstance(row, tuple)
            and row[0] is not None
        ) or isinstance(row, str) or (
            isinstance(row, dict) and row.get("entity") is not None
        )
    ]
    if not valid_entities:
        return

    lines.append(f"{indent}- type: entities")
    if title:
        lines.append(f"{indent}  title: {_yaml_quote(title)}")
    lines.append(f"{indent}  show_header_toggle: false")
    lines.append(f"{indent}  entities:")
    for row in valid_entities:
        if isinstance(row, tuple):
            entity_id, name = row
            lines.append(f"{indent}    - entity: {entity_id}")
            lines.append(f"{indent}      name: {_yaml_quote(name)}")
        elif isinstance(row, dict):
            entity_id = row["entity"]
            lines.append(f"{indent}    - entity: {entity_id}")
            if row.get("name"):
                lines.append(f"{indent}      name: {_yaml_quote(str(row['name']))}")
            if row.get("icon"):
                lines.append(f"{indent}      icon: {_yaml_quote(str(row['icon']))}")
        else:
            lines.append(f"{indent}    - entity: {row}")
    _append_card_mod(lines, _entities_card_style_lines(variant), indent=indent)


def _append_history_graph(
    lines: list[str],
    title: str,
    entities: list[tuple[str | None, str]],
    *,
    hours_to_show: int = 504,
    indent: str = "      ",
    variant: str = "default",
) -> None:
    """Append a history-graph card when at least one entity is available."""

    valid_entities = [(entity_id, name) for entity_id, name in entities if entity_id is not None]
    if not valid_entities:
        return

    lines.append(f"{indent}- type: history-graph")
    lines.append(f"{indent}  title: {_yaml_quote(title)}")
    lines.append(f"{indent}  hours_to_show: {hours_to_show}")
    lines.append(f"{indent}  entities:")
    for entity_id, name in valid_entities:
        lines.append(f"{indent}    - entity: {entity_id}")
        lines.append(f"{indent}      name: {_yaml_quote(name)}")
    _append_card_mod(lines, _history_card_style_lines(variant), indent=indent)


def _append_card_mod(lines: list[str], style_lines: list[str], *, indent: str) -> None:
    """Append a card-mod style block."""

    lines.append(f"{indent}  card_mod:")
    lines.append(f"{indent}    style: |")
    for style_line in style_lines:
        lines.append(f"{indent}      {style_line}")


def _grid_card_style_lines(variant: str) -> list[str]:
    """Return card-mod styles for grid containers."""

    if variant == "status":
        return [
            "ha-card {",
            "  background: transparent;",
            "  border: 0;",
            "  box-shadow: none;",
            "  padding: 0;",
            "}",
            "#root {",
            "  display: grid;",
            "  gap: 8px;",
            "}",
        ]

    background = "#13161f"
    radius = "12px"
    padding = "12px"
    if variant in {"action_buttons", "zone_details"}:
        background = "#10131b"

    return [
        "@import url('https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&display=swap');",
        "ha-card {",
        f"  background: {background};",
        "  border: 1px solid #1c2030;",
        f"  border-radius: {radius};",
        "  box-shadow: none;",
        f"  padding: {padding};",
        "  font-family: 'Geist', sans-serif !important;",
        "}",
        "#root {",
        "  display: grid;",
        "  gap: 8px;",
        "}",
    ]


def _common_style_lines(*, radius: int = 12, background: str = "#13161f") -> list[str]:
    """Return shared card-mod style lines."""

    return [
        "@import url('https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&display=swap');",
        ":host {",
        "  --primary-text-color: #e8eaf0;",
        "  --secondary-text-color: #4a5060;",
        "  --card-primary-font-size: 13px;",
        "  --card-secondary-font-size: 12px;",
        "  --paper-item-icon-color: #2a4a7a;",
        "  --state-icon-color: #2a4a7a;",
        "  --tile-color: #2a4a7a;",
        "  --mdc-theme-primary: #2a4a7a;",
        "  --mdc-icon-size: 18px;",
        "  color: #e8eaf0;",
        "}",
        "ha-card {",
        f"  background: {background};",
        "  border: 1px solid #1c2030;",
        f"  border-radius: {radius}px;",
        "  box-shadow: none;",
        "  color: #e8eaf0;",
        "  font-family: 'Geist', sans-serif !important;",
        "}",
        "ha-state-icon {",
        "  color: #2a4a7a !important;",
        "  opacity: 0.9;",
        "}",
        ".primary, .name {",
        "  color: #e8eaf0 !important;",
        "}",
        ".secondary, .info, .value, .state {",
        "  color: #4a5060 !important;",
        "}",
    ]


def _markdown_card_style_lines(variant: str) -> list[str]:
    """Return card-mod styles for markdown cards."""

    background = "#13161f"
    lines = _common_style_lines(radius=12, background=background)
    lines.extend(
        [
            "ha-card {",
            "  padding: 14px 16px;",
            "}",
            "ha-markdown, ha-markdown-element, .markdown {",
            "  color: #dde2ed;",
            "}",
            "h1, h2, h3, p {",
            "  margin: 0;",
            "}",
            "h1, h2, h3 {",
            "  font-weight: 600 !important;",
            "  color: #f0f2f5 !important;",
            "}",
            "h1 {",
            "  font-size: 15px !important;",
            "  letter-spacing: -0.3px;",
            "}",
            "h2, h3 {",
            "  font-size: 15px !important;",
            "  letter-spacing: -0.2px;",
            "}",
            "p {",
            "  margin-top: 6px;",
            "  font-size: 12px;",
            "  line-height: 1.45;",
            "  color: #4a5060 !important;",
            "}",
            "ul {",
            "  margin: 10px 0 0;",
            "  padding: 0;",
            "  list-style: none;",
            "}",
            "li {",
            "  margin: 0;",
            "}",
        ]
    )
    if variant == "hero":
        lines.extend(
            [
                "ha-card { padding: 18px 18px 14px; background: #10131b; border-radius: 14px; }",
                "h1 { font-size: 18px !important; font-weight: 700 !important; letter-spacing: -0.45px; color: #f5f7fb !important; }",
                "p { font-size: 11px; color: #4a5060 !important; margin-top: 2px; letter-spacing: 0.02em; }",
            ]
        )
    elif variant == "section":
        lines.extend(
            [
                "ha-card { padding: 12px 16px 10px; background: #10131b; border-radius: 14px 14px 0 0; border-bottom: 0; margin-bottom: -8px; }",
                "h2, h3 { font-size: 15px !important; text-transform: none !important; letter-spacing: -0.3px !important; color: #f0f2f5 !important; font-weight: 600 !important; }",
                "p { display: none; }",
            ]
        )
    elif variant == "panel":
        lines.extend(
            [
                "ha-card { padding: 14px 16px 16px; }",
                ".ws-panel { display: flex; flex-direction: column; gap: 14px; }",
                ".ws-panel-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }",
                ".ws-panel-title { font-size: 15px; font-weight: 600; letter-spacing: -0.3px; color: #f0f2f5; }",
                ".ws-panel-subtitle { margin-top: 4px; font-size: 12px; color: #4a5060; line-height: 1.45; }",
                ".ws-panel-meta { padding: 3px 8px; border-radius: 999px; border: 1px solid #1c2030; background: #0d1017; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: #4a5060; white-space: nowrap; }",
                ".ws-metric-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }",
                ".ws-metric-box { padding: 11px 12px; border-radius: 8px; border: 1px solid #1c2030; background: #0c0e12; }",
                ".ws-metric-box.accent { background: #102218; border-color: #193626; }",
                ".ws-metric-label { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: #4a5060; }",
                ".ws-metric-value { margin-top: 6px; font-size: 15px; font-weight: 600; color: #e8eaf0; letter-spacing: -0.3px; }",
                ".ws-metric-box.accent .ws-metric-value { color: #34d87a; }",
                ".ws-metric-unit { margin-left: 4px; font-size: 11px; color: #3a4255; font-weight: 500; }",
                ".ws-trend-list { display: flex; flex-direction: column; gap: 8px; }",
                ".ws-trend-row { display: grid; grid-template-columns: 44px minmax(0, 1fr) auto; align-items: center; gap: 10px; }",
                ".ws-trend-label { font-size: 11px; color: #4a5060; }",
                ".ws-trend-bar { position: relative; height: 6px; border-radius: 999px; background: #0c0e12; border: 1px solid #1c2030; overflow: hidden; }",
                ".ws-trend-fill { position: absolute; inset: 0 auto 0 0; width: var(--fill, 50%); border-radius: inherit; background: var(--fill-color, #2a5a9a); }",
                ".ws-trend-value { font-size: 11px; color: #8a909c; white-space: nowrap; }",
                ".ws-window-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }",
                ".ws-window-cell { padding: 12px; border-radius: 8px; border: 1px solid #1c2030; background: #0c0e12; }",
                ".ws-window-label { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: #4a5060; }",
                ".ws-window-time { margin-top: 8px; font-size: 27px; font-weight: 600; line-height: 1; letter-spacing: -1px; color: #e8eaf0; }",
                ".ws-window-suffix { margin-top: 3px; font-size: 10px; color: #4a5060; text-transform: uppercase; letter-spacing: 0.08em; }",
                ".ws-panel-empty { padding: 14px; border-radius: 8px; border: 1px dashed #1c2030; color: #4a5060; background: #0c0e12; font-size: 12px; }",
                ".ws-note-list { display: flex; flex-direction: column; gap: 8px; }",
                ".ws-note-row { display: flex; justify-content: space-between; gap: 12px; padding: 9px 0; border-top: 1px solid #1c2030; }",
                ".ws-note-row:first-child { border-top: 0; padding-top: 0; }",
                ".ws-note-main { min-width: 0; }",
                ".ws-note-title { font-size: 12px; font-weight: 500; color: #e8eaf0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }",
                ".ws-note-meta { margin-top: 2px; font-size: 11px; color: #4a5060; }",
                ".ws-note-value { font-size: 12px; color: #8a909c; white-space: nowrap; }",
            ]
        )
    elif variant == "zone-list":
        lines.extend(
            [
                "ha-card { padding: 14px 16px 16px; }",
                ".ws-panel { display: flex; flex-direction: column; gap: 14px; }",
                ".ws-panel-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }",
                ".ws-panel-title { font-size: 15px; font-weight: 600; letter-spacing: -0.3px; color: #f0f2f5; }",
                ".ws-panel-subtitle { margin-top: 4px; font-size: 12px; color: #4a5060; line-height: 1.45; }",
                ".ws-panel-meta { padding: 3px 8px; border-radius: 999px; border: 1px solid #1c2030; background: #0d1017; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: #4a5060; white-space: nowrap; }",
                ".ws-zone-summary { display: flex; justify-content: space-between; gap: 12px; font-size: 10px; color: #4a5060; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600; }",
                ".ws-zone-list { display: flex; flex-direction: column; gap: 6px; }",
                ".ws-zone-row { display: grid; grid-template-columns: minmax(0,1fr) auto auto; gap: 10px; align-items: center; padding: 11px 12px; border: 1px solid #1c2030; border-radius: 8px; background: #0c0e12; }",
                ".ws-zone-main { min-width: 0; }",
                ".ws-zone-name { font-size: 13px; font-weight: 500; color: #e8eaf0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }",
                ".ws-zone-meta { margin-top: 2px; font-size: 11px; color: #4a5060; }",
                ".ws-zone-runtime { font-size: 13px; font-weight: 600; color: #4a5060; text-align: right; white-space: nowrap; }",
                ".ws-zone-pill { padding: 3px 8px; border-radius: 999px; font-size: 10px; font-weight: 600; white-space: nowrap; letter-spacing: 0.03em; }",
                ".ws-zone-pill-standard { background: #0d1320; color: #2a4a80; }",
                ".ws-zone-pill-drought { background: #141a08; color: #5a6e18; }",
                ".ws-zone-pill-disabled { background: #13141a; color: #2e3545; }",
                ".ws-zone-pill-veg { background: #0b1823; color: #3b7ca6; }",
                ".ws-zone-live { color: #34d87a; font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; margin-left: 6px; }",
                "@media (max-width: 860px) { .ws-zone-row { grid-template-columns: minmax(0,1fr) auto; } .ws-zone-runtime { grid-column: 2; grid-row: 1 / span 2; align-self: center; } }",
            ]
        )
    elif variant == "zone-header":
        lines.extend(
            [
                "ha-card { padding: 14px 16px 10px; background: #10131b; border-radius: 14px 14px 0 0; border-bottom: 0; margin-bottom: -8px; }",
                ".ws-zone-card-title { font-size: 17px; font-weight: 600; color: #f0f2f5; letter-spacing: -0.3px; }",
                ".ws-zone-card-subtitle { margin-top: 3px; font-size: 10px; color: #4a5060; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600; }",
            ]
        )
    elif variant == "note":
        lines.extend(
            [
                "h2, h3 { font-size: 10px !important; text-transform: uppercase !important; letter-spacing: 0.1em !important; color: #4a5060 !important; font-weight: 600 !important; }",
                "p, li { font-size: 12px; color: #4a5060; line-height: 1.5; }",
                "strong { color: #e8eaf0; font-weight: 600; }",
            ]
        )
    elif variant == "note-list":
        lines.extend(
            [
                "ha-card { padding: 14px 16px 16px; }",
                ".ws-panel { display: flex; flex-direction: column; gap: 14px; }",
                ".ws-panel-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }",
                ".ws-panel-title { font-size: 15px; font-weight: 600; letter-spacing: -0.3px; color: #f0f2f5; }",
                ".ws-panel-subtitle { margin-top: 4px; font-size: 12px; color: #4a5060; line-height: 1.45; }",
                ".ws-note-list { display: flex; flex-direction: column; gap: 8px; }",
                ".ws-note-row { display: flex; justify-content: space-between; gap: 12px; padding-top: 9px; border-top: 1px solid #1c2030; }",
                ".ws-note-row:first-child { border-top: 0; padding-top: 0; }",
                ".ws-note-main { min-width: 0; }",
                ".ws-note-title { font-size: 12px; font-weight: 500; color: #e8eaf0; }",
                ".ws-note-meta { margin-top: 2px; font-size: 11px; color: #4a5060; }",
                ".ws-note-value { font-size: 12px; color: #8a909c; white-space: nowrap; }",
            ]
        )
    return lines


def _tile_card_style_lines(variant: str, name: str) -> list[str]:
    """Return card-mod styles for tile cards."""

    background = "#13161f"
    if variant == "guardrail":
        background = "#0c0e12"
    elif variant == "source":
        background = "#0f1219"
    lines = _common_style_lines(radius=8, background=background)
    lines.extend(
        [
            "ha-card {",
            "  min-height: 64px;",
            "}",
            ".primary {",
            "  font-size: 10px !important;",
            "  font-weight: 600 !important;",
            "  text-transform: uppercase;",
            "  letter-spacing: 0.06em;",
            "  color: #4a5060 !important;",
            "}",
            ".secondary, .state {",
            "  font-size: 13px !important;",
            "  color: #8a909c !important;",
            "}",
        ]
    )
    if variant == "metric":
        if name in {"Daily rain", "Effective 24h"}:
            lines.extend(
                [
                    "ha-card { background: #102218; border-color: #193626; }",
                    ".secondary, .state { color: #34d87a !important; }",
                ]
            )
        lines.extend(
            [
                "ha-card { min-height: 62px; }",
                ".secondary, .state { font-size: 14px !important; font-weight: 600 !important; color: #e8eaf0 !important; letter-spacing: -0.25px; }",
            ]
        )
    elif variant == "status":
        lines.extend(
            [
                "ha-card { min-height: 54px; border-radius: 20px; background: #14161d; }",
                ".primary { text-transform: none; letter-spacing: -0.1px; font-size: 12px !important; color: #8a909c !important; }",
                ".secondary, .state { font-size: 13px !important; font-weight: 600 !important; color: #e8eaf0 !important; }",
            ]
        )
    elif variant == "guardrail":
        lines.extend(
            [
                "ha-card { min-height: 58px; }",
                ".primary { color: #2e3545 !important; }",
                ".secondary, .state { color: #8a909c !important; font-size: 14px !important; font-weight: 600 !important; }",
            ]
        )
    elif variant == "toggle":
        lines.extend(
            [
                "ha-card { min-height: 56px; }",
                ".primary { color: #e8eaf0 !important; text-transform: none; letter-spacing: -0.1px; font-size: 13px !important; }",
                ".secondary, .state { color: #4a5060 !important; font-size: 12px !important; }",
            ]
        )
    elif variant == "zone":
        lines.extend(
            [
                "ha-card { min-height: 64px; }",
                ".primary { text-transform: none; letter-spacing: -0.1px; font-size: 12px !important; color: #e8eaf0 !important; }",
                ".secondary, .state { font-size: 13px !important; font-weight: 600 !important; color: #8a909c !important; }",
            ]
        )
    elif variant == "source":
        lines.extend(
            [
                "ha-card { min-height: 58px; }",
                ".primary { text-transform: none; letter-spacing: -0.1px; font-size: 12px !important; color: #e8eaf0 !important; }",
                ".secondary, .state { font-size: 12px !important; color: #8a909c !important; }",
            ]
        )
    elif variant == "time":
        lines.extend(
            [
                "ha-card { min-height: 68px; background: #0c0e12; }",
                ".primary { text-transform: none; letter-spacing: -0.1px; font-size: 11px !important; color: #4a5060 !important; }",
                ".secondary, .state { font-size: 16px !important; font-weight: 600 !important; color: #e8eaf0 !important; letter-spacing: -0.35px; }",
            ]
        )
    return lines


def _button_card_style_lines(variant: str, name: str) -> list[str]:
    """Return card-mod styles for button cards."""

    is_stop = "Stop" in name
    background = "#1a0d0c" if is_stop else "#0f1219"
    text = "#a03530" if is_stop else "#e8eaf0"
    icon_color = "#a03530" if is_stop else "#2a4a7a"
    lines = _common_style_lines(radius=8, background=background)
    lines.extend(
        [
            "ha-card {",
            "  min-height: 52px;",
            "  height: 52px;",
            "  border: 1px solid #1c2030;",
            "}",
            ".name, .primary, span {",
            f"  color: {text} !important;",
            "  font-size: 11px !important;",
            "  font-weight: 500 !important;",
            "  text-transform: none;",
            "  letter-spacing: -0.1px;",
            "}",
            ".icon, ha-state-icon {",
            f"  color: {icon_color} !important;",
            "  opacity: 0.9;",
            "  transform: scale(0.82);",
            "}",
        ]
    )
    return lines


def _history_card_style_lines(variant: str) -> list[str]:
    """Return card-mod styles for history graphs."""

    lines = _common_style_lines(radius=12, background="#13161f")
    lines.extend(
        [
            "ha-card {",
            "  overflow: hidden;",
            "}",
            ".card-header {",
            "  font-size: 15px !important;",
            "  font-weight: 600 !important;",
            "  text-transform: none !important;",
            "  letter-spacing: -0.3px !important;",
            "  color: #f0f2f5 !important;",
            "  padding-bottom: 10px !important;",
            "}",
            "svg text {",
            "  fill: #3a4255 !important;",
            "}",
            ".labels, .legend, .axis, .chartLegend, .chartLabels {",
            "  color: #3a4255 !important;",
            "}",
        ]
    )
    return lines


def _logbook_card_style_lines() -> list[str]:
    """Return card-mod styles for recent-watering logbook cards."""

    lines = _common_style_lines(radius=12, background="#13161f")
    lines.extend(
        [
            "ha-card { overflow: hidden; }",
            ".card-header { font-size: 15px !important; font-weight: 600 !important; letter-spacing: -0.3px !important; color: #f0f2f5 !important; padding-bottom: 8px !important; }",
            "ha-logbook { --mdc-theme-text-primary-on-background: #e8eaf0; }",
            ".entry-container, .entry, .entry-content, .message, .name { color: #e8eaf0 !important; }",
            ".when, .domain, .secondary, .metadata { color: #4a5060 !important; }",
        ]
    )
    return lines


def _entities_card_style_lines(variant: str) -> list[str]:
    """Return card-mod styles for entities cards."""

    lines = _common_style_lines(radius=12, background="#13161f")
    lines.extend(
        [
            "ha-card { padding: 8px 12px 10px; }",
            ".card-header { font-size: 15px !important; font-weight: 600 !important; letter-spacing: -0.3px !important; color: #f0f2f5 !important; padding-bottom: 8px !important; }",
            ".name { color: #e8eaf0 !important; font-size: 13px !important; }",
            ".secondary, .state, .value { color: #8a909c !important; font-size: 12px !important; }",
            "hui-generic-entity-row, hui-select-entity-row, hui-number-entity-row, hui-time-entity-row, hui-toggle-entity-row { --paper-item-icon-color: #2a4a7a; --state-icon-color: #2a4a7a; }",
        ]
    )
    if variant == "zones":
        lines.extend(
            [
                "ha-card { padding: 14px 16px 16px; }",
                ".card-header { display: none; }",
                ".name { color: #e8eaf0 !important; font-size: 13px !important; font-weight: 500 !important; }",
                ".secondary, .state, .value { color: #4a5060 !important; font-size: 12px !important; font-weight: 600 !important; }",
                "hui-generic-entity-row { min-height: 54px; padding: 5px 0; border-top: 1px solid #1c2030; }",
                "hui-generic-entity-row:first-of-type { border-top: 0; }",
            ]
        )
    return lines


def _overview_header_markdown_lines(
    controller_name: str,
) -> list[str]:
    """Return overview header markdown."""

    return [
        f"# {controller_name}",
        "",
        "B-hyve · Home Assistant",
    ]


def _section_markdown_lines(title: str, subtitle: str) -> list[str]:
    """Return a styled section heading block."""

    if subtitle:
        return [
            f"## {title}",
            "",
            subtitle,
        ]
    return [f"## {title}"]


def _zone_card_header_markdown_lines(name: str, zone_number: int) -> list[str]:
    """Return zone card header markdown."""

    return [
        f"## {name}",
        "",
        f"Zone {zone_number}",
    ]


def _projected_cycle_markdown_lines(
    next_cycle_entity: str,
    zone_dashboard_data: list[dict[str, str | int | None]],
) -> list[str]:
    """Return markdown for the projected next cycle."""

    zone_deficit_rows = [
        (
            str(zone_data["name"]),
            str(zone_data["deficit"]),
        )
        for zone_data in zone_dashboard_data
        if zone_data["deficit"] is not None
    ]

    lines = [
        f"{{% set cycle = states('{next_cycle_entity}') %}}",
        f"{{% set attrs = state_attr('{next_cycle_entity}', 'projected_zone_runs') or [] %}}",
        f"{{% set plan_label = state_attr('{next_cycle_entity}', 'plan_label') or state_attr('{next_cycle_entity}', 'status') or cycle %}}",
        f"{{% set reason = state_attr('{next_cycle_entity}', 'reason') %}}",
        f"{{% set earliest_start = state_attr('{next_cycle_entity}', 'projected_start_local') %}}",
        f"{{% set earliest_end = state_attr('{next_cycle_entity}', 'projected_end_local') %}}",
        f"{{% set estimated_start = state_attr('{next_cycle_entity}', 'estimated_next_need_start_local') %}}",
        f"{{% set estimated_end = state_attr('{next_cycle_entity}', 'estimated_next_need_end_local') %}}",
        f"{{% set fallback_highest_deficit = state_attr('{next_cycle_entity}', 'highest_zone_deficit_inches') %}}",
        f"{{% set fallback_peak_zone = state_attr('{next_cycle_entity}', 'peak_deficit_zone_name') %}}",
        "### Plan For Next Cycle",
        "",
        "{% if cycle in ['unavailable', 'unknown', 'none', 'not_configured'] %}",
        "Planner data is not currently available.",
        "{% else %}",
        "**Plan:** {{ plan_label }}",
        "",
        "{% if reason %}",
        "**Why:** {{ reason }}",
        "",
        "{% endif %}",
        "{% set ns = namespace(highest = none, peak_zone = none) %}",
    ]

    for zone_name, deficit_entity in zone_deficit_rows:
        lines.extend(
            [
                f"{{% set zone_state = states('{deficit_entity}') %}}",
                "{% if zone_state not in ['unknown', 'unavailable', 'none', 'None', ''] %}",
                f"{{% set zone_name = {_yaml_quote(zone_name)} %}}",
                "{% set zone_deficit = zone_state | float(0) %}",
                "{% if ns.highest is none or zone_deficit > ns.highest %}",
                "{% set ns.highest = zone_deficit %}",
                "{% set ns.peak_zone = zone_name %}",
                "{% endif %}",
                "{% endif %}",
            ]
        )

    lines.extend(
        [
            "{% set highest_deficit = ns.highest if ns.highest is not none else fallback_highest_deficit %}",
            "{% set peak_zone = ns.peak_zone if ns.peak_zone is not none else fallback_peak_zone %}",
            "**Highest deficit now:** {{ highest_deficit }} in{% if peak_zone %} ({{ peak_zone }}){% endif %}",
        "",
        "{% if earliest_start and earliest_end %}",
        "**Earliest allowed window:** {{ earliest_start }} -> {{ earliest_end }}",
        "",
        "{% endif %}",
        "{% if estimated_start and estimated_end and estimated_start != earliest_start %}",
        "**Likely next watering need:** {{ estimated_start }} -> {{ estimated_end }}",
        "",
        "{% elif estimated_start and not earliest_start %}",
        "**Likely next watering need:** {{ estimated_start }} -> {{ estimated_end }}",
        "",
        "{% endif %}",
        "{% if attrs %}",
        "{% for item in attrs[:4] %}",
        "- **{{ item.zone_name }}** — {{ item.runtime_minutes }} min{% if item.runtime_bank_minutes %} (bank {{ item.runtime_bank_minutes }} min){% endif %}",
        "{% endfor %}",
        "{% endif %}",
        "{% endif %}",
        ]
    )

    return lines


def _watering_window_markdown_lines(
    *,
    effective_start_entity: str | None,
    effective_end_entity: str | None,
    suggested_start_entity: str | None,
    suggested_end_entity: str | None,
) -> list[str]:
    """Return a clearer watering-window summary for the overview."""

    if (
        effective_start_entity is None
        or effective_end_entity is None
        or suggested_start_entity is None
        or suggested_end_entity is None
    ):
        return ["Watering-window entities are not currently available."]

    return [
        f"{{% set decision = state_attr('{effective_start_entity}', 'decision') %}}",
        f"{{% set auto_enabled = state_attr('{effective_start_entity}', 'automatic_window_enabled') %}}",
        f"{{% set preference = state_attr('{effective_start_entity}', 'automatic_window_preference') %}}",
        f"{{% set total_runtime = state_attr('{effective_start_entity}', 'total_recommended_runtime_minutes') | int(0) %}}",
        f"{{% set active_start = states('{effective_start_entity}') %}}",
        f"{{% set active_end = states('{effective_end_entity}') %}}",
        f"{{% set suggested_start = states('{suggested_start_entity}') %}}",
        f"{{% set suggested_end = states('{suggested_end_entity}') %}}",
        "{% if decision == 'skip' and total_runtime <= 0 %}",
        "**No watering window is active today.**",
        "",
        "{% if auto_enabled %}",
        "{% set anchor_label = 'sunset' if preference == 'Evening (sunset)' else 'sunrise' %}",
        "No zones need water, {{ anchor_label }} at **{{ active_end if preference == 'Morning (dawn)' else active_start }}**.",
        "{% else %}",
        "No zones need water right now. Your fixed manual window remains **{{ active_start }} -> {{ active_end }}** if you choose to water manually.",
        "{% endif %}",
        "{% else %}",
        "**Active window:** {{ active_start }} -> {{ active_end }}",
        "",
        "**Suggested window:** {{ suggested_start }} -> {{ suggested_end }}",
        "{% endif %}",
    ]


def _weekly_runtime_markdown_lines(
    zone_dashboard_data: list[dict[str, str | int | None]],
) -> list[str]:
    """Return markdown for per-zone watering totals this week."""

    zone_items: list[str] = []
    for zone in zone_dashboard_data:
        runtime_entity = (
            zone["runtime_this_week"]
            or zone["recommended_runtime"]
            or zone["deficit"]
        )
        if not runtime_entity:
            continue
        zone_items.append(
            "  {'name': "
            + json.dumps(str(zone["label"]))
            + ", 'entity': "
            + json.dumps(str(runtime_entity))
            + "},"
        )

    if not zone_items:
        return [
            "### Time Watered This Week by Zone",
            "",
            "No zone runtime data is currently available.",
        ]

    return [
        "{% set zones = [",
        *zone_items,
        "] %}",
        "### Time Watered This Week by Zone",
        "",
        "{% for item in zones %}",
        "- **{{ item.name }}** — {{ state_attr(item.entity, 'recent_runtime_minutes_7d') or 0 }} min",
        "{% endfor %}",
    ]


def _compact_zone_name(name: str, controller_name: str | None = None) -> str:
    """Return a shorter display label for overview tables."""

    compact = _strip_zone_name_prefix(name, controller_name)
    compact = compact.replace("Front yard ", "Front ")
    compact = compact.replace("Backyard ", "Back ")
    compact = compact.replace("(", "").replace(")", "")
    compact = compact.replace("Middle-strip", "Middle")
    compact = compact.replace("driveway", "Drive")
    return compact


def _strip_zone_name_prefix(name: str, controller_name: str | None = None) -> str:
    """Remove the controller name from B-hyve zone labels when it is duplicated."""

    compact = name.strip()
    if not controller_name:
        return compact

    prefix = controller_name.strip()
    if not prefix:
        return compact

    if compact.casefold().startswith(prefix.casefold()):
        stripped = compact[len(prefix) :].lstrip(" -–—:·")
        if stripped:
            return stripped
    return compact


def _profile_summary_label(profile: str) -> str:
    """Return the short dashboard label for a watering profile."""

    profile = normalize_zone_watering_profile(profile)
    if profile == ZONE_WATERING_PROFILE_DROUGHT_TOLERANT:
        return "Drought"
    if profile == ZONE_WATERING_PROFILE_TREES_SHRUBS:
        return "Trees"
    if profile == ZONE_WATERING_PROFILE_VEGETABLE_GARDEN:
        return "Veg"
    if profile == ZONE_WATERING_PROFILE_DISABLED:
        return "Disabled"
    return "Lawn"


def _profile_icon(profile: str) -> str:
    """Return a compact icon for a watering profile."""

    profile = normalize_zone_watering_profile(profile)
    if profile == ZONE_WATERING_PROFILE_DROUGHT_TOLERANT:
        return "mdi:leaf"
    if profile == ZONE_WATERING_PROFILE_TREES_SHRUBS:
        return "mdi:tree-outline"
    if profile == ZONE_WATERING_PROFILE_VEGETABLE_GARDEN:
        return "mdi:carrot"
    if profile == ZONE_WATERING_PROFILE_DISABLED:
        return "mdi:cancel"
    return "mdi:sprinkler-variant"


def _yaml_quote(value: str) -> str:
    """Return a YAML-safe quoted scalar."""

    return json.dumps(value)
