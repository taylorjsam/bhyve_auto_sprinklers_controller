"""Diagnostics support for B-hyve Auto Sprinklers Controller."""

from __future__ import annotations

from dataclasses import asdict

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.diagnostics import async_redact_data

from .const import CONF_CONTROLLER_DEVICE_ID
from .models import BhyveSprinklersConfigEntry

TO_REDACT = {
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_CONTROLLER_DEVICE_ID,
    "device_id",
    "orbit_session_token",
}


async def async_get_config_entry_diagnostics(
    hass,
    entry: BhyveSprinklersConfigEntry,
) -> dict:
    """Return diagnostics for a config entry."""

    del hass
    controllers = [
        {
            **asdict(controller),
            "zones": [asdict(zone) for zone in controller.zones],
            "active_run": (
                {
                    **asdict(controller.active_run),
                    "started_at": controller.active_run.started_at.isoformat(),
                    "expected_end": controller.active_run.expected_end.isoformat(),
                }
                if controller.active_run is not None
                else None
            ),
        }
        for controller in entry.runtime_data.coordinator.data.controllers
    ]
    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": dict(entry.options),
        "runtime_data": {
            "device_count": entry.runtime_data.coordinator.data.device_count,
            "sprinkler_controllers": async_redact_data(controllers, TO_REDACT),
            "last_update_success": entry.runtime_data.coordinator.last_update_success,
        },
    }


async def async_get_device_diagnostics(
    hass,
    entry: BhyveSprinklersConfigEntry,
    device: DeviceEntry,
) -> dict:
    """Return diagnostics for a device entry."""

    del device
    return await async_get_config_entry_diagnostics(hass, entry)
