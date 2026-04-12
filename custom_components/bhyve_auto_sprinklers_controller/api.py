"""Direct Orbit/B-hyve API client."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
import json
import logging
import time
from typing import Any

from aiohttp import (
    ClientConnectionError,
    ClientResponseError,
    ClientSession,
    ClientWebSocketResponse,
    WSMsgType,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .const import CONF_CONTROLLER_DEVICE_ID
from .models import BhyveSprinklerController

_LOGGER = logging.getLogger(__name__)

API_HOST = "https://api.orbitbhyve.com"
WS_HOST = "wss://api.orbitbhyve.com/v1/events"
LOGIN_PATH = "/v1/session"
DEVICES_PATH = "/v1/devices"
DEVICE_HISTORY_PATH = "/v1/watering_events/{}"
TIMER_PROGRAMS_PATH = "/v1/sprinkler_timer_programs"
LANDSCAPE_DESCRIPTIONS_PATH = "/v1/landscape_descriptions"
DEVICE_TYPE_BRIDGE = "bridge"
DEVICE_TYPE_SPRINKLER = "sprinkler_timer"
API_POLL_PERIOD_SECONDS = 300


class BhyveApiError(Exception):
    """Raised when an Orbit/B-hyve request fails."""


class BhyveAuthenticationError(BhyveApiError):
    """Raised when Orbit/B-hyve rejects credentials."""


class BhyveApiClient:
    """Small direct client for the Orbit/B-hyve cloud API."""

    def __init__(
        self,
        username: str,
        password: str,
        session: ClientSession,
    ) -> None:
        """Initialize the API client."""

        self._username = username
        self._password = password
        self._session = session
        self._token: str | None = None
        self._devices: list[dict[str, Any]] = []
        self._last_devices_poll = 0.0
        self._histories: dict[str, list[dict[str, Any]]] = {}
        self._last_history_polls: dict[str, float] = {}
        self._programs: list[dict[str, Any]] = []
        self._last_programs_poll = 0.0
        self._websocket: ClientWebSocketResponse | None = None
        self._websocket_lock = asyncio.Lock()
        self._listen_task: asyncio.Task[None] | None = None
        self._event_callback: Any | None = None

    @property
    def token(self) -> str | None:
        """Return the current Orbit session token."""

        return self._token

    async def async_login(self) -> None:
        """Authenticate and store the Orbit session token."""

        url = f"{API_HOST}{LOGIN_PATH}"
        payload = {"session": {"email": self._username, "password": self._password}}
        try:
            async with self._session.post(url, json=payload) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
        except ClientResponseError as err:
            if err.status in {400, 401, 403}:
                raise BhyveAuthenticationError("Invalid B-hyve credentials") from err
            raise BhyveApiError(f"Unable to authenticate with B-hyve: {err}") from err
        except (ClientConnectionError, TimeoutError, OSError) as err:
            raise BhyveApiError(f"Unable to connect to B-hyve: {err}") from err

        token = data.get("orbit_session_token")
        if not token:
            raise BhyveApiError("B-hyve login did not return an Orbit session token")
        self._token = str(token)

    async def async_close(self) -> None:
        """Close background websocket resources."""

        if self._listen_task is not None:
            self._listen_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listen_task
            self._listen_task = None
        if self._websocket is not None and not self._websocket.closed:
            await self._websocket.close()
        self._websocket = None

    async def async_request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_payload: Mapping[str, Any] | None = None,
    ) -> Any:
        """Make an authenticated REST request."""

        if self._token is None:
            await self.async_login()

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json; charset=utf-8;",
            "Referer": API_HOST,
            "Orbit-Session-Token": self._token or "",
            "User-Agent": "Home Assistant B-hyve Auto Sprinklers Controller",
        }
        url = f"{API_HOST}{endpoint}"
        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                params=dict(params or {}),
                json=dict(json_payload or {}) if json_payload is not None else None,
            ) as response:
                response.raise_for_status()
                return await response.json(content_type=None)
        except ClientResponseError as err:
            if err.status in {401, 403}:
                raise BhyveAuthenticationError("B-hyve session is no longer valid") from err
            raise BhyveApiError(f"B-hyve request failed for {endpoint}: {err}") from err
        except (ClientConnectionError, TimeoutError, OSError) as err:
            raise BhyveApiError(f"Unable to communicate with B-hyve: {err}") from err

    async def async_get_devices(self, *, force_update: bool = False) -> list[dict[str, Any]]:
        """Return account devices."""

        now = time.monotonic()
        if force_update or now - self._last_devices_poll >= API_POLL_PERIOD_SECONDS:
            devices = await self.async_request(
                "get",
                DEVICES_PATH,
                params={"t": str(time.time())},
            )
            self._devices = list(devices or []) if isinstance(devices, list) else []
            self._last_devices_poll = now
        return self._devices

    async def async_get_programs(self, *, force_update: bool = False) -> list[dict[str, Any]]:
        """Return timer programs."""

        now = time.monotonic()
        if force_update or now - self._last_programs_poll >= API_POLL_PERIOD_SECONDS:
            programs = await self.async_request(
                "get",
                TIMER_PROGRAMS_PATH,
                params={"t": str(time.time())},
            )
            self._programs = list(programs or []) if isinstance(programs, list) else []
            self._last_programs_poll = now
        return self._programs

    async def async_get_device_history(
        self,
        device_id: str,
        *,
        force_update: bool = False,
    ) -> list[dict[str, Any]]:
        """Return recent watering history for a controller."""

        now = time.monotonic()
        last_poll = self._last_history_polls.get(device_id, 0.0)
        if force_update or now - last_poll >= API_POLL_PERIOD_SECONDS:
            history = await self.async_request(
                "get",
                DEVICE_HISTORY_PATH.format(device_id),
                params={"t": str(time.time()), "page": "1", "per-page": "25"},
            )
            self._histories[device_id] = (
                list(history or []) if isinstance(history, list) else []
            )
            self._last_history_polls[device_id] = now
        return self._histories.get(device_id, [])

    async def async_update_device(self, device: Mapping[str, Any]) -> None:
        """Update device settings."""

        device_id = device.get("id")
        if not device_id:
            raise BhyveApiError("Cannot update a B-hyve device without an id")
        await self.async_request(
            "put",
            f"{DEVICES_PATH}/{device_id}",
            json_payload={"device": dict(device)},
        )

    async def async_send_message(self, payload: Mapping[str, Any]) -> None:
        """Send a controller command over the B-hyve event websocket."""

        async with self._websocket_lock:
            websocket = await self._async_ensure_websocket()
            await websocket.send_str(json.dumps(dict(payload)))

    async def async_start_zone(
        self,
        device_id: str,
        zone_number: int,
        duration_seconds: int,
    ) -> None:
        """Start a single station for the requested duration."""

        minutes = max(1, int(round(duration_seconds / 60)))
        payload = {
            "event": "change_mode",
            "mode": "manual",
            "device_id": device_id,
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "stations": [{"station": int(zone_number), "run_time": minutes}],
        }
        await self.async_send_message(payload)

    async def async_stop_watering(self, device_id: str) -> None:
        """Stop watering on a controller."""

        payload = {
            "event": "change_mode",
            "mode": "manual",
            "device_id": device_id,
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "stations": [],
        }
        await self.async_send_message(payload)

    async def async_listen(self, callback: Any) -> None:
        """Register an optional websocket event callback."""

        self._event_callback = callback
        await self._async_ensure_websocket()

    async def _async_ensure_websocket(self) -> ClientWebSocketResponse:
        """Open and authenticate the event websocket if needed."""

        if self._token is None:
            await self.async_login()
        if self._websocket is not None and not self._websocket.closed:
            return self._websocket

        self._websocket = await self._session.ws_connect(WS_HOST)
        await self._websocket.send_str(
            json.dumps(
                {
                    "event": "app_connection",
                    "orbit_session_token": self._token,
                }
            )
        )
        self._listen_task = asyncio.create_task(self._async_websocket_listener())
        return self._websocket

    async def _async_websocket_listener(self) -> None:
        """Drain websocket events and forward them when a callback is registered."""

        assert self._websocket is not None
        async for msg in self._websocket:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    _LOGGER.debug("Ignoring non-JSON B-hyve websocket message: %s", msg.data)
                    continue
                if self._event_callback is not None:
                    await self._event_callback(data)
            elif msg.type in {WSMsgType.CLOSED, WSMsgType.ERROR}:
                break


def normalize_credentials(data: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize user supplied credentials before storing them."""

    return {
        CONF_USERNAME: str(data[CONF_USERNAME]).strip().lower(),
        CONF_PASSWORD: str(data[CONF_PASSWORD]),
        CONF_CONTROLLER_DEVICE_ID: str(data.get(CONF_CONTROLLER_DEVICE_ID, "")).strip(),
    }


async def async_login_and_get_client(
    data: Mapping[str, Any],
    session: ClientSession,
) -> BhyveApiClient:
    """Create and authenticate a B-hyve API client."""

    client = BhyveApiClient(
        str(data[CONF_USERNAME]).strip().lower(),
        str(data[CONF_PASSWORD]),
        session,
    )
    await client.async_login()
    return client


async def async_authenticate(client: BhyveApiClient) -> None:
    """Authenticate a B-hyve API client."""

    await client.async_login()


async def async_get_account_devices(client: BhyveApiClient) -> list[dict[str, Any]]:
    """Return raw account devices."""

    return await client.async_get_devices(force_update=True)


def discover_sprinkler_controllers(
    devices: list[dict[str, Any]],
) -> tuple[BhyveSprinklerController, ...]:
    """Extract sprinkler controllers from B-hyve account devices."""

    controllers: dict[str, BhyveSprinklerController] = {}
    for device in devices:
        if device.get("type") != DEVICE_TYPE_SPRINKLER:
            continue
        device_id = _safe_string(device.get("id"))
        if not device_id:
            continue
        nickname = _safe_string(device.get("name")) or "B-hyve Sprinkler Controller"
        controllers[device_id] = BhyveSprinklerController(
            mac=device_id,
            nickname=nickname,
            product_model=_safe_string(device.get("hardware_version")),
            product_type=_safe_string(device.get("type")),
            device_type=_safe_string(device.get("type")),
            available=_safe_bool(device.get("is_connected")),
        )

    return tuple(
        sorted(
            controllers.values(),
            key=lambda controller: (
                controller.nickname.casefold(),
                controller.mac.casefold(),
            ),
        )
    )


def serialize_controllers(
    controllers: tuple[BhyveSprinklerController, ...],
) -> list[dict[str, Any]]:
    """Convert controller dataclasses into plain dictionaries."""

    return [asdict(controller) for controller in controllers]


def _safe_string(value: Any) -> str | None:
    """Return a cleaned string or None."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_bool(value: Any) -> bool | None:
    """Best-effort conversion for optional boolean-like values."""

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
