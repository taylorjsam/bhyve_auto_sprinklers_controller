"""Select entities for irrigation automation settings."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    AUTOMATIC_WINDOW_PREFERENCE_EVENING,
    AUTOMATIC_WINDOW_PREFERENCE_MORNING,
    CONF_DAILY_RAIN_ENTITY_ID,
    CONF_FORECAST_WEATHER_ENTITY_ID,
    CONF_HUMIDITY_ENTITY_ID,
    CONF_IRRADIANCE_ENTITY_ID,
    CONF_NOTIFICATION_SERVICE,
    CONF_TEMPERATURE_ENTITY_ID,
    CONF_UV_INDEX_ENTITY_ID,
    CONF_WIND_GUST_ENTITY_ID,
    CONF_WIND_SPEED_ENTITY_ID,
    DAY_RESTRICTION_AUTO,
    DAY_RESTRICTION_DISABLED,
    DEFAULT_AUTOMATIC_WINDOW_PREFERENCE,
    DOMAIN,
    WEEKDAY_KEYS,
    WEEKDAY_LABELS,
    normalize_automatic_window_preference,
    normalize_day_restriction,
    normalize_zone_watering_profile,
    SPRINKLER_WIND_PROFILE_DRIP_BUBBLER,
    SPRINKLER_WIND_PROFILE_ROTARY_STREAM,
    SPRINKLER_WIND_PROFILE_STANDARD_SPRAY,
    ZONE_AGRONOMY_DEFAULTS,
    ZONE_WATERING_PROFILE_ANNUAL_FLOWERS,
    ZONE_WATERING_PROFILE_DEFAULT,
    ZONE_WATERING_PROFILE_DISABLED,
    ZONE_WATERING_PROFILE_DROUGHT_TOLERANT,
    ZONE_WATERING_PROFILE_NATIVE_XERISCAPE,
    ZONE_WATERING_PROFILE_TREES_SHRUBS,
    ZONE_WATERING_PROFILE_VEGETABLE_GARDEN,
)
from .entity import BhyveControllerCoordinatorEntity, BhyveZoneCoordinatorEntity
from .models import BhyveSprinklersConfigEntry

NOT_CONFIGURED_OPTION = "Not configured"


async def async_setup_entry(
    hass,
    entry: BhyveSprinklersConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities."""

    entities: list[SelectEntity] = [
        BhyveNotificationTargetSelect(hass, entry),
        BhyveAccountSourceSelect(
            hass,
            entry,
            CONF_DAILY_RAIN_ENTITY_ID,
            "Daily rain source",
            "mdi:weather-rainy",
            ("sensor", "number", "input_number"),
        ),
        BhyveAccountSourceSelect(
            hass,
            entry,
            CONF_FORECAST_WEATHER_ENTITY_ID,
            "Forecast weather source",
            "mdi:weather-partly-rainy",
            ("weather",),
        ),
        BhyveAccountSourceSelect(
            hass,
            entry,
            CONF_IRRADIANCE_ENTITY_ID,
            "Solar radiation source",
            "mdi:weather-sunny-alert",
            ("sensor", "number", "input_number"),
        ),
        BhyveAccountSourceSelect(
            hass,
            entry,
            CONF_UV_INDEX_ENTITY_ID,
            "Current UV index source",
            "mdi:white-balance-sunny",
            ("sensor", "number", "input_number"),
        ),
        BhyveAccountSourceSelect(
            hass,
            entry,
            CONF_HUMIDITY_ENTITY_ID,
            "Current humidity source",
            "mdi:water-percent",
            ("sensor", "number", "input_number"),
        ),
        BhyveAccountSourceSelect(
            hass,
            entry,
            CONF_TEMPERATURE_ENTITY_ID,
            "Current temperature source",
            "mdi:thermometer",
            ("sensor", "number", "input_number"),
        ),
        BhyveAccountSourceSelect(
            hass,
            entry,
            CONF_WIND_SPEED_ENTITY_ID,
            "Current wind speed source",
            "mdi:weather-windy",
            ("sensor", "number", "input_number"),
        ),
        BhyveAccountSourceSelect(
            hass,
            entry,
            CONF_WIND_GUST_ENTITY_ID,
            "Current wind gust source",
            "mdi:weather-windy-variant",
            ("sensor", "number", "input_number"),
        ),
    ]
    for controller in entry.runtime_data.coordinator.data.controllers:
        entities.append(
            BhyveControllerAutomaticWindowPreferenceSelect(
                entry,
                controller.device_id,
            )
        )
        for weekday_key in WEEKDAY_KEYS:
            entities.append(
                BhyveControllerWateringDaySelect(
                    entry,
                    controller.device_id,
                    weekday_key,
                )
            )
        for zone in controller.zones:
            entities.append(
                BhyveZoneWateringProfileSelect(
                    entry,
                    controller.device_id,
                    zone.zone_number,
                )
            )
            entities.append(
                BhyveZoneSprinklerWindProfileSelect(
                    entry,
                    controller.device_id,
                    zone.zone_number,
                )
            )
    async_add_entities(entities)


class BhyveNotificationTargetSelect(RestoreEntity, SelectEntity):
    """Choose which Home Assistant notify service receives plan messages."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_icon = "mdi:message-badge-outline"
    _attr_name = "Notification target"

    def __init__(self, hass, entry: BhyveSprinklersConfigEntry) -> None:
        """Initialize the select entity."""

        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_notification_target"
        configured_option = entry.options.get(CONF_NOTIFICATION_SERVICE)
        self._current_option = (
            str(configured_option)
            if configured_option
            else "notify.notify"
        )
        self._entry.runtime_data.notification_service = self._current_option

    async def async_added_to_hass(self) -> None:
        """Restore the last selected notify service when possible."""

        await super().async_added_to_hass()
        configured_option = self._entry.options.get(CONF_NOTIFICATION_SERVICE)
        options = self.options
        if configured_option in options:
            self._current_option = str(configured_option)
        last_state = await self.async_get_last_state()
        if (
            configured_option not in options
            and last_state is not None
            and last_state.state in options
        ):
            self._current_option = last_state.state
        elif self._current_option not in options and options:
            self._current_option = options[0]
        self._entry.runtime_data.notification_service = self._current_option

    @property
    def current_option(self) -> str:
        """Return the selected notify service."""

        return self._current_option

    @property
    def options(self) -> list[str]:
        """Return the current set of notify services."""

        return self._current_options()

    async def async_select_option(self, option: str) -> None:
        """Update the selected notify service."""

        if option not in self.options:
            raise ValueError(f"Unsupported notification target: {option}")
        self._current_option = option
        self._entry.runtime_data.notification_service = option
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the select entity to the account device."""

        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            manufacturer="Orbit B-hyve",
            model="Cloud Account",
            name="B-hyve Account",
        )

    def _current_options(self) -> list[str]:
        """Return available notify services as fully qualified service names."""

        notify_services = self.hass.services.async_services().get("notify", {})
        options = sorted(f"notify.{name}" for name in notify_services)
        configured_option = self._entry.options.get(CONF_NOTIFICATION_SERVICE)
        if configured_option and str(configured_option) not in options:
            options.append(str(configured_option))
            options.sort()
        if self._current_option not in options:
            options.append(self._current_option)
            options.sort()
        if not options:
            return ["notify.notify"]
        if "notify.notify" not in options:
            options.insert(0, "notify.notify")
        return options


class BhyveAccountSourceSelect(SelectEntity):
    """Choose a Home Assistant entity to feed planner inputs."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True

    def __init__(
        self,
        hass,
        entry: BhyveSprinklersConfigEntry,
        option_key: str,
        entity_name: str,
        icon: str,
        allowed_domains: Iterable[str],
    ) -> None:
        """Initialize the account-level source selector."""

        self.hass = hass
        self._entry = entry
        self._option_key = option_key
        self._attr_name = entity_name
        self._attr_icon = icon
        self._allowed_domains = tuple(allowed_domains)
        self._attr_unique_id = f"{entry.entry_id}_{option_key}_source"
        self._current_option = NOT_CONFIGURED_OPTION

    async def async_added_to_hass(self) -> None:
        """Restore the selected source from config-entry options."""

        configured = self._configured_option
        if configured is not None:
            self._current_option = configured

    @property
    def current_option(self) -> str:
        """Return the selected source entity id."""

        return self._current_option

    @property
    def options(self) -> list[str]:
        """Return selectable entity ids plus a not-configured sentinel."""

        configured = self._configured_option
        entity_ids = sorted(
            state.entity_id
            for state in self.hass.states.async_all()
            if state.entity_id.split(".", 1)[0] in self._allowed_domains
        )
        options = [NOT_CONFIGURED_OPTION, *entity_ids]
        if configured is not None and configured not in options:
            options.append(configured)
        return options

    async def async_select_option(self, option: str) -> None:
        """Persist the selected source entity to config-entry options."""

        if option not in self.options:
            raise ValueError(f"Unsupported source entity: {option}")

        updated_options = dict(self._entry.options)
        if option == NOT_CONFIGURED_OPTION:
            updated_options.pop(self._option_key, None)
            self._current_option = NOT_CONFIGURED_OPTION
        else:
            updated_options[self._option_key] = option
            self._current_option = option

        self.hass.config_entries.async_update_entry(
            self._entry,
            options=updated_options,
        )
        self.async_write_ha_state()
        await self.hass.config_entries.async_reload(self._entry.entry_id)

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the select entity to the account device."""

        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            manufacturer="Orbit B-hyve",
            model="Cloud Account",
            name="B-hyve Account",
        )

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose selection hints for the UI."""

        return {
            "configure_via": "B-hyve Account device > Configuration",
            "allowed_domains": list(self._allowed_domains),
        }

    @property
    def _configured_option(self) -> str | None:
        """Return the configured source entity id when one exists."""

        value = self._entry.options.get(self._option_key)
        if not value:
            return None
        return str(value)


class BhyveZoneWateringProfileSelect(
    BhyveZoneCoordinatorEntity,
    RestoreEntity,
    SelectEntity,
):
    """Choose the planner watering profile for a zone."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_icon = "mdi:sprout-outline"
    _attr_name = "Watering profile"
    _attr_options = [
        ZONE_WATERING_PROFILE_DEFAULT,
        ZONE_WATERING_PROFILE_DISABLED,
        ZONE_WATERING_PROFILE_TREES_SHRUBS,
        ZONE_WATERING_PROFILE_DROUGHT_TOLERANT,
        ZONE_WATERING_PROFILE_VEGETABLE_GARDEN,
        ZONE_WATERING_PROFILE_ANNUAL_FLOWERS,
        ZONE_WATERING_PROFILE_NATIVE_XERISCAPE,
    ]

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the zone profile select."""

        super().__init__(entry, device_id, zone_number)
        self._entry = entry
        self._attr_unique_id = f"{device_id}_{zone_number}_watering_profile"
        self._current_option = ZONE_WATERING_PROFILE_DEFAULT

    async def async_added_to_hass(self) -> None:
        """Restore the last zone profile when available."""

        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        runtime_key = self._runtime_key
        stored_option = self._entry.runtime_data.zone_watering_profiles.get(runtime_key)
        stored_option = normalize_zone_watering_profile(stored_option)
        if stored_option in self.options:
            self._current_option = stored_option
        elif last_state is not None:
            restored_option = normalize_zone_watering_profile(last_state.state)
            if restored_option in self.options:
                self._current_option = restored_option
        self._entry.runtime_data.zone_watering_profiles[runtime_key] = self._current_option
        self._apply_profile_defaults(self._current_option, only_if_missing=True)
        if (
            self._current_option != ZONE_WATERING_PROFILE_DEFAULT
            and self._entry.runtime_data.plan_coordinator is not None
        ):
            await self._entry.runtime_data.plan_coordinator.async_request_refresh()

    @property
    def current_option(self) -> str:
        """Return the selected zone profile."""

        return self._current_option

    async def async_select_option(self, option: str) -> None:
        """Update the selected zone watering profile."""

        if option not in self.options:
            raise ValueError(f"Unsupported watering profile: {option}")
        self._current_option = option
        self._entry.runtime_data.zone_watering_profiles[self._runtime_key] = option
        self._apply_profile_defaults(option, only_if_missing=False)
        self.async_write_ha_state()
        if self._entry.runtime_data.plan_coordinator is not None:
            await self._entry.runtime_data.plan_coordinator.async_request_refresh()

    @property
    def name(self) -> str | None:
        """Return the entity name."""

        zone = self.zone
        if zone is None:
            return f"Zone {self._zone_number} watering profile"
        return f"{zone.name} watering profile"

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Explain what each profile means."""

        return {
            ZONE_WATERING_PROFILE_DEFAULT: "Use the standard lawn-oriented planner behavior for the zone",
            ZONE_WATERING_PROFILE_DISABLED: "Exclude this zone from all irrigation planning and recommendations",
            ZONE_WATERING_PROFILE_TREES_SHRUBS: "Favor deeper, less-frequent watering for established trees and shrubs",
            ZONE_WATERING_PROFILE_DROUGHT_TOLERANT: "Favor less-frequent watering for low-water-use plantings",
            ZONE_WATERING_PROFILE_VEGETABLE_GARDEN: "Favor shorter, more-frequent watering for raised-bed vegetables",
            ZONE_WATERING_PROFILE_ANNUAL_FLOWERS: "Favor shallow-rooted annuals and containers that need frequent light replenishment",
            ZONE_WATERING_PROFILE_NATIVE_XERISCAPE: "Favor very low-water-use native and xeriscape plantings",
        }

    @property
    def _runtime_key(self) -> str:
        """Return the shared runtime-data key for this zone."""

        return f"{self._device_id}:{self._zone_number}"

    def _apply_profile_defaults(self, profile: str, *, only_if_missing: bool) -> None:
        """Seed explicit agronomy values from the selected profile."""

        defaults = ZONE_AGRONOMY_DEFAULTS.get(
            normalize_zone_watering_profile(profile),
            ZONE_AGRONOMY_DEFAULTS[ZONE_WATERING_PROFILE_DEFAULT],
        )
        runtime_key = self._runtime_key
        if not only_if_missing or runtime_key not in self._entry.runtime_data.zone_root_depths:
            self._entry.runtime_data.zone_root_depths[runtime_key] = defaults["root_depth_in"]
        if not only_if_missing or runtime_key not in self._entry.runtime_data.zone_soil_whc:
            self._entry.runtime_data.zone_soil_whc[runtime_key] = defaults["soil_whc_in_per_in"]
        if not only_if_missing or runtime_key not in self._entry.runtime_data.zone_mad_values:
            self._entry.runtime_data.zone_mad_values[runtime_key] = defaults["mad"]
        if not only_if_missing or runtime_key not in self._entry.runtime_data.zone_kc_values:
            self._entry.runtime_data.zone_kc_values[runtime_key] = defaults["kc"]


class BhyveControllerWateringDaySelect(
    BhyveControllerCoordinatorEntity,
    RestoreEntity,
    SelectEntity,
):
    """Choose whether a controller is allowed to water on a given weekday."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-week"
    _attr_options = [
        DAY_RESTRICTION_AUTO,
        DAY_RESTRICTION_DISABLED,
    ]

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        weekday_key: str,
    ) -> None:
        """Initialize the controller weekday selector."""

        super().__init__(entry, device_id)
        self._entry = entry
        self._weekday_key = weekday_key
        self._attr_name = f"{WEEKDAY_LABELS[weekday_key]} watering day"
        self._attr_unique_id = f"{device_id}_{weekday_key}_watering_day"
        self._current_option = DAY_RESTRICTION_AUTO

    async def async_added_to_hass(self) -> None:
        """Restore the selected controller weekday mode."""

        await super().async_added_to_hass()
        stored_option = normalize_day_restriction(
            self._entry.runtime_data.controller_watering_day_restrictions.get(
                self._runtime_key
            )
        )
        if stored_option in self.options:
            self._current_option = stored_option
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self.options:
            self._current_option = last_state.state
        self._entry.runtime_data.controller_watering_day_restrictions[self._runtime_key] = (
            self._current_option
        )
        if (
            self._current_option != DAY_RESTRICTION_AUTO
            and self._entry.runtime_data.plan_coordinator is not None
        ):
            await self._entry.runtime_data.plan_coordinator.async_request_refresh()

    @property
    def current_option(self) -> str:
        """Return the current weekday mode."""

        return self._current_option

    async def async_select_option(self, option: str) -> None:
        """Update the controller weekday mode."""

        if option not in self.options:
            raise ValueError(f"Unsupported watering day mode: {option}")
        self._current_option = option
        self._entry.runtime_data.controller_watering_day_restrictions[
            self._runtime_key
        ] = option
        self.async_write_ha_state()
        if self._entry.runtime_data.plan_coordinator is not None:
            await self._entry.runtime_data.plan_coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Explain how the weekday restriction behaves."""

        return {
            DAY_RESTRICTION_AUTO: "Allow controller watering on this weekday",
            DAY_RESTRICTION_DISABLED: "Disallow all controller watering on this weekday",
        }

    @property
    def _runtime_key(self) -> str:
        """Return the shared runtime-data key for this controller weekday."""

        return f"{self._device_id}:{self._weekday_key}"


class BhyveControllerAutomaticWindowPreferenceSelect(
    BhyveControllerCoordinatorEntity,
    RestoreEntity,
    SelectEntity,
):
    """Choose whether automatic watering should anchor to dawn or sunset."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_icon = "mdi:weather-sunset-up"
    _attr_name = "Automatic watering time"
    _attr_options = [
        AUTOMATIC_WINDOW_PREFERENCE_MORNING,
        AUTOMATIC_WINDOW_PREFERENCE_EVENING,
    ]

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
    ) -> None:
        """Initialize the controller automatic-window preference selector."""

        super().__init__(entry, device_id)
        self._entry = entry
        self._attr_unique_id = f"{device_id}_automatic_window_preference"
        self._current_option = DEFAULT_AUTOMATIC_WINDOW_PREFERENCE

    async def async_added_to_hass(self) -> None:
        """Restore the selected automatic watering timing preference."""

        await super().async_added_to_hass()
        stored_option = normalize_automatic_window_preference(
            self._entry.runtime_data.automatic_window_preferences.get(self._device_id)
        )
        if stored_option in self.options:
            self._current_option = stored_option
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self.options:
            self._current_option = last_state.state
        self._entry.runtime_data.automatic_window_preferences[self._device_id] = (
            self._current_option
        )
        if (
            self._current_option != DEFAULT_AUTOMATIC_WINDOW_PREFERENCE
            and self._entry.runtime_data.plan_coordinator is not None
        ):
            await self._entry.runtime_data.plan_coordinator.async_request_refresh()

    @property
    def current_option(self) -> str:
        """Return the selected automatic watering timing preference."""

        return self._current_option

    async def async_select_option(self, option: str) -> None:
        """Update the automatic watering timing preference."""

        if option not in self.options:
            raise ValueError(f"Unsupported watering timing preference: {option}")
        self._current_option = option
        self._entry.runtime_data.automatic_window_preferences[self._device_id] = option
        self.async_write_ha_state()
        if self._entry.runtime_data.plan_coordinator is not None:
            await self._entry.runtime_data.plan_coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Explain how each automatic watering timing preference behaves."""

        return {
            AUTOMATIC_WINDOW_PREFERENCE_MORNING: (
                "Anchor automatic watering to finish as close to dawn as practical"
            ),
            AUTOMATIC_WINDOW_PREFERENCE_EVENING: (
                "Anchor automatic watering to start as close to sunset as practical"
            ),
        }


class BhyveZoneWateringDaySelect(
    BhyveZoneCoordinatorEntity,
    RestoreEntity,
    SelectEntity,
):
    """Choose whether a zone is allowed to water on a given weekday."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-week-begin"
    _attr_options = [
        DAY_RESTRICTION_AUTO,
        DAY_RESTRICTION_DISABLED,
    ]

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
        weekday_key: str,
    ) -> None:
        """Initialize the zone weekday selector."""

        super().__init__(entry, device_id, zone_number)
        self._entry = entry
        self._weekday_key = weekday_key
        self._attr_name = f"{WEEKDAY_LABELS[weekday_key]} watering day"
        self._attr_unique_id = f"{device_id}_{zone_number}_{weekday_key}_watering_day"
        self._current_option = DAY_RESTRICTION_AUTO

    async def async_added_to_hass(self) -> None:
        """Restore the selected zone weekday mode."""

        await super().async_added_to_hass()
        stored_option = normalize_day_restriction(
            self._entry.runtime_data.zone_watering_day_restrictions.get(
                self._runtime_key
            )
        )
        if stored_option in self.options:
            self._current_option = stored_option
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self.options:
            self._current_option = last_state.state
        self._entry.runtime_data.zone_watering_day_restrictions[self._runtime_key] = (
            self._current_option
        )
        if (
            self._current_option != DAY_RESTRICTION_AUTO
            and self._entry.runtime_data.plan_coordinator is not None
        ):
            await self._entry.runtime_data.plan_coordinator.async_request_refresh()

    @property
    def current_option(self) -> str:
        """Return the current weekday mode."""

        return self._current_option

    async def async_select_option(self, option: str) -> None:
        """Update the zone weekday mode."""

        if option not in self.options:
            raise ValueError(f"Unsupported watering day mode: {option}")
        self._current_option = option
        self._entry.runtime_data.zone_watering_day_restrictions[self._runtime_key] = option
        self.async_write_ha_state()
        if self._entry.runtime_data.plan_coordinator is not None:
            await self._entry.runtime_data.plan_coordinator.async_request_refresh()

    @property
    def name(self) -> str | None:
        """Return the entity name."""

        zone = self.zone
        if zone is None:
            return f"Zone {self._zone_number} {WEEKDAY_LABELS[self._weekday_key]} watering day"
        return f"{zone.name} {WEEKDAY_LABELS[self._weekday_key]} watering day"

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Explain how the zone weekday restriction behaves."""

        return {
            DAY_RESTRICTION_AUTO: "Follow the controller's weekday watering rule for this day",
            DAY_RESTRICTION_DISABLED: "Never water this zone on this weekday, even if the controller allows it",
        }

    @property
    def _runtime_key(self) -> str:
        """Return the shared runtime-data key for this zone weekday."""

        return f"{self._device_id}:{self._zone_number}:{self._weekday_key}"


class BhyveZoneSprinklerWindProfileSelect(
    BhyveZoneCoordinatorEntity,
    RestoreEntity,
    SelectEntity,
):
    """Choose the sprinkler head type for an individual zone."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_icon = "mdi:sprinkler-variant"
    _attr_name = "Sprinkler head type"
    _attr_options = [
        SPRINKLER_WIND_PROFILE_STANDARD_SPRAY,
        SPRINKLER_WIND_PROFILE_ROTARY_STREAM,
        SPRINKLER_WIND_PROFILE_DRIP_BUBBLER,
    ]

    def __init__(
        self,
        entry: BhyveSprinklersConfigEntry,
        device_id: str,
        zone_number: int,
    ) -> None:
        """Initialize the zone-level sprinkler head type selector."""

        super().__init__(entry, device_id, zone_number)
        self._entry = entry
        self._attr_unique_id = f"{device_id}_{zone_number}_sprinkler_wind_profile"
        self._current_option = SPRINKLER_WIND_PROFILE_STANDARD_SPRAY

    async def async_added_to_hass(self) -> None:
        """Restore the last selected sprinkler head type."""

        await super().async_added_to_hass()
        stored_option = self._entry.runtime_data.zone_sprinkler_wind_profiles.get(
            self._runtime_key
        )
        if stored_option in self.options:
            self._current_option = stored_option
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self.options:
            self._current_option = last_state.state
        self._entry.runtime_data.zone_sprinkler_wind_profiles[self._runtime_key] = (
            self._current_option
        )
        if self._entry.runtime_data.plan_coordinator is not None:
            await self._entry.runtime_data.plan_coordinator.async_request_refresh()

    @property
    def current_option(self) -> str:
        """Return the selected sprinkler head type."""

        return self._current_option

    async def async_select_option(self, option: str) -> None:
        """Update the zone-level sprinkler head type."""

        if option not in self.options:
            raise ValueError(f"Unsupported sprinkler head type: {option}")
        self._current_option = option
        self._entry.runtime_data.zone_sprinkler_wind_profiles[self._runtime_key] = option
        self.async_write_ha_state()
        if self._entry.runtime_data.plan_coordinator is not None:
            await self._entry.runtime_data.plan_coordinator.async_request_refresh()

    @property
    def name(self) -> str | None:
        """Return the entity name."""

        zone = self.zone
        if zone is None:
            return f"Zone {self._zone_number} sprinkler head type"
        return f"{zone.name} sprinkler head type"

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Explain the purpose of each sprinkler head type option."""

        return {
            SPRINKLER_WIND_PROFILE_STANDARD_SPRAY: "Use the conservative wind stop best suited to fixed spray heads",
            SPRINKLER_WIND_PROFILE_ROTARY_STREAM: "Allow a slightly higher wind limit for rotary and larger-droplet stream nozzles",
            SPRINKLER_WIND_PROFILE_DRIP_BUBBLER: "Never wind-hold low-trajectory drip emitters or bubbler-style irrigation",
        }

    @property
    def _runtime_key(self) -> str:
        """Return the shared runtime-data key for this zone."""

        return f"{self._device_id}:{self._zone_number}"
