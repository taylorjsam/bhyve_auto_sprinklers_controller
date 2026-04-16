"""Coordinator for B-hyve sprinkler state."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import logging

from aiohttp import ClientError
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .irrigation_api import (
    BhyveIrrigationApi,
    BhyveIrrigationApiError,
    BhyveIrrigationAuthenticationError,
)
from .models import (
    BhyveActiveRun,
    BhyveIrrigationSnapshot,
    BhyveSprinklerControllerSnapshot,
)

_LOGGER = logging.getLogger(__name__)


class BhyveIrrigationCoordinator(DataUpdateCoordinator[BhyveIrrigationSnapshot]):
    """Refresh sprinkler controller and zone state."""

    def __init__(
        self,
        hass: HomeAssistant,
        irrigation_api: BhyveIrrigationApi,
        manual_device_id: str | None,
        username: str,
    ) -> None:
        """Initialize the coordinator."""

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{username}",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self._irrigation_api = irrigation_api
        self._manual_device_id = manual_device_id
        self._optimistic_runs: dict[str, BhyveActiveRun] = {}
        self._scheduled_refreshes: dict[str, object] = {}
        self._sequence_tasks: dict[str, asyncio.Task[None]] = {}

    async def async_quick_run_zone(
        self,
        device_id: str,
        zone_number: int,
        duration: int,
        *,
        source: str = "quick_run",
        replace_existing: bool = True,
    ) -> None:
        """Start a quick run on a zone and refresh coordinator state."""

        if replace_existing:
            await self._async_replace_current_run(device_id)
        else:
            await self._async_cancel_sequence_task(device_id)

        await self._async_start_zone(
            device_id,
            zone_number,
            duration,
            source=source,
        )

    async def async_run_zone_sequence(
        self,
        device_id: str,
        zone_runs: list[tuple[int, int]],
        *,
        source: str = "planned_cycle",
    ) -> None:
        """Run multiple zones sequentially, replacing any current watering."""

        normalized_runs = [
            (int(zone_number), int(duration))
            for zone_number, duration in zone_runs
            if int(duration) > 0
        ]
        if not normalized_runs:
            raise HomeAssistantError("No zones are currently recommended to run")

        await self._async_replace_current_run(device_id)

        first_zone, first_duration = normalized_runs[0]
        await self._async_start_zone(
            device_id,
            first_zone,
            first_duration,
            source=source,
        )

        if len(normalized_runs) <= 1:
            return

        task = self.hass.async_create_task(
            self._async_continue_sequence(
                device_id,
                normalized_runs[1:],
                delay_seconds=first_duration,
                source=source,
            )
        )
        self._sequence_tasks[device_id] = task

    async def _async_start_zone(
        self,
        device_id: str,
        zone_number: int,
        duration: int,
        *,
        source: str,
    ) -> None:
        """Start a zone and push optimistic state."""

        await self._irrigation_api.async_quick_run_zone(device_id, zone_number, duration)
        now = datetime.now(UTC)
        self._optimistic_runs[device_id] = BhyveActiveRun(
            zone_number=zone_number,
            duration=duration,
            started_at=now,
            expected_end=now + timedelta(seconds=duration),
            source=source,
        )
        self._schedule_expiry_refresh(device_id)
        self._async_push_optimistic_update()
        await self.async_request_refresh()

    async def async_stop_watering(self, device_id: str) -> None:
        """Stop watering on a controller and refresh coordinator state."""

        await self._async_cancel_sequence_task(device_id)
        if self._controller_active_run(device_id) is None:
            try:
                await self._irrigation_api.async_stop_watering(device_id)
            except BhyveIrrigationApiError:
                _LOGGER.debug(
                    "B-hyve did not report an active run while stopping %s",
                    device_id,
                    exc_info=True,
                )
            self._clear_optimistic_run(device_id)
            self._async_push_optimistic_update()
            await self.async_request_refresh()
            return

        await self._irrigation_api.async_stop_watering(device_id)
        self._clear_optimistic_run(device_id)
        self._async_push_optimistic_update()
        await self.async_request_refresh()

    def get_controller(self, device_id: str) -> BhyveSprinklerControllerSnapshot | None:
        """Return the controller snapshot for a device id."""

        if self.data is None:
            return None

        for controller in self.data.controllers:
            if controller.device_id == device_id:
                return controller
        return None

    async def _async_update_data(self) -> BhyveIrrigationSnapshot:
        """Fetch sprinkler controllers and their zones."""

        try:
            controller_snapshots = await self._irrigation_api.async_get_controllers(
                self._manual_device_id
            )
        except BhyveIrrigationAuthenticationError as err:
            raise ConfigEntryAuthFailed(
                "B-hyve authentication failed. Please reconfigure the integration."
            ) from err
        except BhyveIrrigationApiError as err:
            raise UpdateFailed(str(err)) from err
        except (ClientError, OSError) as err:
            raise UpdateFailed(f"Error communicating with B-hyve: {err}") from err

        return BhyveIrrigationSnapshot(
            device_count=len(controller_snapshots),
            controllers=self._apply_optimistic_runs(controller_snapshots),
        )

    def _get_active_run(self, device_id: str) -> BhyveActiveRun | None:
        """Return the current optimistic active run, clearing it when expired."""

        active_run = self._optimistic_runs.get(device_id)
        if active_run is None:
            return None

        if active_run.expected_end <= datetime.now(UTC):
            self._clear_optimistic_run(device_id)
            return None

        return active_run

    def _controller_active_run(
        self,
        device_id: str,
    ) -> BhyveActiveRun | None:
        """Return the best-known active run for a controller."""

        optimistic_run = self._get_active_run(device_id)
        if optimistic_run is not None:
            return optimistic_run

        controller = self.get_controller(device_id)
        if controller is None:
            return None
        return controller.active_run

    async def _async_replace_current_run(self, device_id: str) -> None:
        """Replace the current watering activity on a controller."""

        await self._async_cancel_sequence_task(device_id)
        if self._controller_active_run(device_id) is None:
            self._clear_optimistic_run(device_id)
            self._async_push_optimistic_update()
            return

        await self._irrigation_api.async_stop_watering(device_id)
        self._clear_optimistic_run(device_id)
        self._async_push_optimistic_update()
        await self.async_request_refresh()
        await asyncio.sleep(1)

    async def _async_cancel_sequence_task(self, device_id: str) -> None:
        """Cancel any queued sequential watering task for a controller."""

        task = self._sequence_tasks.pop(device_id, None)
        if task is None:
            return

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _async_continue_sequence(
        self,
        device_id: str,
        remaining_runs: list[tuple[int, int]],
        *,
        delay_seconds: int,
        source: str,
    ) -> None:
        """Continue a sequential watering cycle after the current zone finishes."""

        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(max(1, int(delay_seconds)) + 1)
            for index, (zone_number, duration) in enumerate(remaining_runs):
                if self._sequence_tasks.get(device_id) is not current_task:
                    return
                await self._async_start_zone(
                    device_id,
                    zone_number,
                    duration,
                    source=source,
                )
                if index < len(remaining_runs) - 1:
                    await asyncio.sleep(max(1, int(duration)) + 1)
        finally:
            if self._sequence_tasks.get(device_id) is current_task:
                self._sequence_tasks.pop(device_id, None)

    def _schedule_expiry_refresh(self, device_id: str) -> None:
        """Refresh state after an optimistic quick run should have finished."""

        self._clear_scheduled_refresh(device_id)
        active_run = self._optimistic_runs.get(device_id)
        if active_run is None:
            return

        @callback
        def _handle_expiry(now: datetime) -> None:
            self.hass.async_create_task(self._async_handle_expiry_refresh(now))

        self._scheduled_refreshes[device_id] = async_track_point_in_utc_time(
            self.hass,
            _handle_expiry,
            active_run.expected_end + timedelta(seconds=1),
        )

    async def _async_handle_expiry_refresh(self, now: datetime) -> None:
        """Refresh coordinator state once a quick run has likely finished."""

        del now
        expired_device_ids = [
            device_id
            for device_id, active_run in self._optimistic_runs.items()
            if active_run.expected_end <= datetime.now(UTC)
        ]
        for device_id in expired_device_ids:
            self._clear_optimistic_run(device_id)

        self._async_push_optimistic_update()
        await self.async_request_refresh()

    def _clear_optimistic_run(self, device_id: str) -> None:
        """Clear optimistic run state for a device."""

        self._optimistic_runs.pop(device_id, None)
        self._clear_scheduled_refresh(device_id)

    def _clear_scheduled_refresh(self, device_id: str) -> None:
        """Clear any scheduled expiry refresh for a device."""

        unsub = self._scheduled_refreshes.pop(device_id, None)
        if unsub is not None:
            unsub()

    def _async_push_optimistic_update(self) -> None:
        """Push an in-memory snapshot update with optimistic run data."""

        if self.data is None:
            return

        controllers = self._apply_optimistic_runs(self.data.controllers)
        self.async_set_updated_data(replace(self.data, controllers=controllers))

    def _apply_optimistic_runs(
        self,
        controllers: tuple[BhyveSprinklerControllerSnapshot, ...],
    ) -> tuple[BhyveSprinklerControllerSnapshot, ...]:
        """Overlay active quick runs without discarding live service state."""

        return tuple(
            replace(
                controller,
                active_run=self._get_active_run(controller.device_id)
                or controller.active_run,
            )
            for controller in controllers
        )
