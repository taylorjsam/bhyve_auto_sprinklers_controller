"""B-hyve irrigation wrapper backed by the direct Orbit cloud client."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from .api import (
    BhyveApiClient,
    BhyveApiError,
    BhyveAuthenticationError,
    DEVICE_TYPE_SPRINKLER,
)
from .models import (
    BhyveActiveRun,
    BhyveLatestEvent,
    BhyvePlantSubtype,
    BhyveScheduleSummary,
    BhyveSprinklerControllerSnapshot,
    BhyveSprinklerZone,
)

_LOGGER = logging.getLogger(__name__)


class BhyveIrrigationApiError(Exception):
    """Raised when irrigation service calls fail."""


class BhyveIrrigationAuthenticationError(BhyveIrrigationApiError):
    """Raised when Bhyve authentication fails."""


class BhyveIrrigationApi:
    """Hardware adapter for Orbit/B-hyve sprinkler controllers."""

    def __init__(self, client: BhyveApiClient) -> None:
        """Initialize the irrigation API wrapper."""

        self._client = client

    async def async_get_controllers(
        self,
        manual_device_id: str | None = None,
    ) -> tuple[BhyveSprinklerControllerSnapshot, ...]:
        """Return all B-hyve sprinkler controllers visible to the account."""

        try:
            devices = await self._client.async_get_devices()
        except BhyveAuthenticationError as err:
            raise BhyveIrrigationAuthenticationError(str(err)) from err
        except BhyveApiError as err:
            raise BhyveIrrigationApiError(str(err)) from err

        snapshots: list[BhyveSprinklerControllerSnapshot] = []
        for device in devices:
            if device.get("type") != DEVICE_TYPE_SPRINKLER:
                continue
            device_id = _as_str(device.get("id"))
            if not device_id:
                continue
            if manual_device_id and device_id != manual_device_id:
                continue

            try:
                history = await self._client.async_get_device_history(device_id)
            except BhyveApiError as err:
                _LOGGER.debug(
                    "Unable to load B-hyve watering history for %s: %s",
                    device_id,
                    err,
                )
                history = []

            zones = tuple(
                self._parse_zone(device_id, zone, history)
                for zone in device.get("zones", []) or []
            )

            snapshots.append(
                BhyveSprinklerControllerSnapshot(
                    device_id=device_id,
                    nickname=_as_str(device.get("name"))
                    or "B-hyve Sprinkler Controller",
                    product_model=_as_str(device.get("hardware_version")),
                    product_type=_as_str(device.get("type")),
                    device_type=_as_str(device.get("type")),
                    available=_as_bool(device.get("is_connected")),
                    zones=zones,
                    active_run=self._parse_active_run(device),
                    last_error=None,
                )
            )

        if manual_device_id and not snapshots:
            raise BhyveIrrigationApiError(
                "Configured B-hyve controller id was not found on this account"
            )

        return tuple(snapshots)

    async def async_quick_run_zone(
        self,
        device_id: str,
        zone_number: int,
        duration: int,
    ) -> None:
        """Start a quick run on a specific zone."""

        try:
            await self._client.async_start_zone(device_id, zone_number, duration)
        except BhyveAuthenticationError as err:
            raise BhyveIrrigationAuthenticationError(str(err)) from err
        except BhyveApiError as err:
            raise BhyveIrrigationApiError(
                f"Unable to start zone {zone_number} on controller {device_id}: {err}"
            ) from err

    async def async_stop_watering(self, device_id: str) -> None:
        """Stop active watering on a controller."""

        try:
            await self._client.async_stop_watering(device_id)
        except BhyveAuthenticationError as err:
            raise BhyveIrrigationAuthenticationError(str(err)) from err
        except BhyveApiError as err:
            raise BhyveIrrigationApiError(
                f"Unable to stop watering on controller {device_id}: {err}"
            ) from err

    @staticmethod
    def _parse_active_run(device: dict[str, Any]) -> BhyveActiveRun | None:
        """Convert device watering status into a lightweight active-run model."""

        status = device.get("status") or {}
        watering_status = status.get("watering_status") or {}
        current_station = _as_int(watering_status.get("current_station"))
        if current_station is None:
            return None

        started_at = _parse_datetime(watering_status.get("started_watering_station_at"))
        if started_at is None:
            started_at = datetime.now(UTC)

        duration_minutes = 0
        stations = watering_status.get("stations") or []
        for station in stations:
            if _as_int(station.get("station")) == current_station:
                duration_minutes = _as_int(station.get("run_time")) or 0
                break

        duration_seconds = max(0, duration_minutes * 60)
        expected_end = started_at + timedelta(seconds=duration_seconds or 3600)
        return BhyveActiveRun(
            zone_number=current_station,
            duration=duration_seconds,
            started_at=started_at,
            expected_end=expected_end,
            source=_as_str(watering_status.get("program")) or "bhyve_status",
        )

    def _parse_zone(
        self,
        device_id: str,
        payload: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> BhyveSprinklerZone:
        """Parse a raw B-hyve zone payload."""

        zone_number = _as_int(payload.get("station")) or _as_int(payload.get("zone")) or 0
        recent_events = self._parse_recent_events_for_zone(history, zone_number)
        latest_event = recent_events[0] if recent_events else None

        quickrun_duration = _as_int(payload.get("manual_preset_runtime"))
        if quickrun_duration is None:
            quickrun_duration = _as_int(payload.get("run_time"))
        if quickrun_duration is not None:
            quickrun_duration *= 60

        return BhyveSprinklerZone(
            device_id=device_id,
            zone_id=str(zone_number),
            zone_number=zone_number,
            name=_as_str(payload.get("name")) or f"Zone {zone_number}",
            enabled=_as_bool(payload.get("enabled")) is not False,
            area=_as_float(payload.get("area")),
            crop_type=None,
            crop_coefficient=None,
            manual_crop_coefficient=None,
            root_depth=None,
            manual_root_depth=None,
            available_water_capacity=None,
            manage_allow_depletion=None,
            exposure_type=None,
            soil_type=None,
            slope_type=None,
            nozzle_type=_as_str(payload.get("sprinkler_type")),
            flow_rate=None,
            efficiency=None,
            number_of_sprinkler_heads=None,
            wired=None,
            smart_duration=None,
            quickrun_duration=quickrun_duration,
            smart_schedule_id=None,
            soil_moisture_level_at_end_of_day_pct=None,
            zone_disable_reason=None,
            garden_subtypes=(),
            tree_subtypes=(),
            latest_event=latest_event,
            recent_events=recent_events,
            schedules=(),
        )

    @staticmethod
    def _parse_recent_events_for_zone(
        history: list[dict[str, Any]],
        zone_number: int,
    ) -> tuple[BhyveLatestEvent, ...]:
        """Extract recent watering events for one zone from B-hyve history."""

        events: list[BhyveLatestEvent] = []
        for history_item in history:
            for irrigation in history_item.get("irrigation", []) or []:
                if _as_int(irrigation.get("station")) != zone_number:
                    continue

                duration_minutes = _as_int(irrigation.get("run_time"))
                started_at = _parse_datetime(
                    irrigation.get("start_time")
                    or history_item.get("start_time")
                    or history_item.get("created_at")
                )
                end_at = (
                    started_at + timedelta(minutes=duration_minutes or 0)
                    if started_at is not None
                    else None
                )
                events.append(
                    BhyveLatestEvent(
                        duration=(
                            duration_minutes * 60
                            if duration_minutes is not None
                            else None
                        ),
                        end_local=end_at.isoformat() if end_at is not None else None,
                        end_ts=int(end_at.timestamp()) if end_at is not None else None,
                        schedule_name=_as_str(history_item.get("program_name")),
                        schedule_type=_as_str(history_item.get("program")) or "bhyve",
                    )
                )

        events.sort(key=lambda event: event.end_ts or 0, reverse=True)
        return tuple(events)


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an Orbit timestamp into an aware UTC datetime."""

    if value is None:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _as_bool(value: Any) -> bool | None:
    """Convert a value to bool when possible."""

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    return None


def _as_float(value: Any) -> float | None:
    """Convert a value to a float when possible."""

    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    """Convert a value to an int when possible."""

    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str(value: Any) -> str | None:
    """Convert a value to a non-empty string when possible."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None
