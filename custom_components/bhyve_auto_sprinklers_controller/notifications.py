"""Shared planner notification helpers."""

from __future__ import annotations

from datetime import datetime

from .models import BhyveControllerPlan, BhyveSprinklersConfigEntry
from .planner import calc_daily_et_progress_fraction


def _notification_service_parts(
    entry: BhyveSprinklersConfigEntry,
) -> tuple[str, str] | None:
    """Return the configured notify service split into domain and service."""

    if not entry.runtime_data.notifications_enabled:
        return None
    service_name = entry.runtime_data.notification_service
    if not service_name or "." not in service_name:
        return None
    domain, service = service_name.split(".", 1)
    return domain, service


def _build_plan_notification_message(controller_plan: BhyveControllerPlan) -> str:
    """Build a compact planner summary message."""

    zone_lines = [
        f"{zone_plan.zone_name}: {zone_plan.recommended_runtime_minutes} min"
        for zone_plan in controller_plan.zone_plans
        if zone_plan.recommended_runtime_minutes > 0
    ]
    if not zone_lines:
        zone_lines = ["No zones are currently recommended to run."]

    return (
        f"{controller_plan.nickname}: {controller_plan.decision}\n"
        f"Reason: {controller_plan.reason}\n"
        f"Deficit: {controller_plan.deficit_inches:.2f} in\n"
        f"Forecast: {controller_plan.forecast_rain_amount_inches or 0:.2f} in\n"
        f"Window: {controller_plan.effective_start_time}-{controller_plan.effective_end_time}\n"
        + "\n".join(zone_lines)
    )


async def async_send_plan_notification_for_plan(
    entry: BhyveSprinklersConfigEntry,
    controller_plan: BhyveControllerPlan,
) -> bool:
    """Send a planner summary for a specific controller plan."""

    service_parts = _notification_service_parts(entry)
    if service_parts is None:
        return False

    domain, service = service_parts
    await entry.runtime_data.coordinator.hass.services.async_call(
        domain,
        service,
        {
            "title": "B-hyve irrigation plan",
            "message": _build_plan_notification_message(controller_plan),
        },
        blocking=False,
    )
    return True


async def async_maybe_send_plan_notification(
    entry: BhyveSprinklersConfigEntry,
    device_id: str,
) -> None:
    """Send the current plan summary for one controller."""

    plan_coordinator = entry.runtime_data.plan_coordinator
    if plan_coordinator is None:
        return

    controller_plan = plan_coordinator.get_controller_plan(device_id)
    if controller_plan is None:
        return

    await async_send_plan_notification_for_plan(entry, controller_plan)


async def async_maybe_send_post_sunset_plan_notifications(
    entry: BhyveSprinklersConfigEntry,
    controller_plans: tuple[BhyveControllerPlan, ...],
    *,
    now_local: datetime,
    latitude: float,
    longitude: float,
) -> None:
    """Send one automatic summary per controller after sunset each local day."""

    if _notification_service_parts(entry) is None:
        return
    if calc_daily_et_progress_fraction(now_local, latitude, longitude) < 1.0:
        return

    date_key = now_local.date().isoformat()
    for controller_plan in controller_plans:
        if (
            entry.runtime_data.last_sunset_notification_dates.get(controller_plan.device_id)
            == date_key
        ):
            continue
        sent = await async_send_plan_notification_for_plan(entry, controller_plan)
        if sent:
            entry.runtime_data.last_sunset_notification_dates[controller_plan.device_id] = (
                date_key
            )
