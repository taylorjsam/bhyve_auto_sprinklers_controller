"""Config flow for the B-hyve Auto Sprinklers Controller integration."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientConnectionError
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    BhyveApiClient,
    BhyveApiError,
    BhyveAuthenticationError,
    async_get_account_devices,
    discover_sprinkler_controllers,
    normalize_credentials,
)
from .const import CONF_CONTROLLER_DEVICE_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(TextSelectorConfig()),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_CONTROLLER_DEVICE_ID): TextSelector(TextSelectorConfig()),
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for B-hyve Auto Sprinklers Controller."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""

        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the initial step."""

        if user_input is not None:
            return await self._async_handle_user_step("user", user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle reauthentication requests from Home Assistant."""

        del entry_data
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Prompt for updated B-hyve credentials during reauth."""

        if user_input is not None:
            return await self._async_handle_user_step("reauth_confirm", user_input)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA,
                self._get_suggested_user_input(),
            ),
        )

    def _get_suggested_user_input(self) -> dict[str, Any]:
        """Return suggested values for reauth forms."""

        if self._reauth_entry is None:
            return {}

        return {
            CONF_USERNAME: self._reauth_entry.data.get(CONF_USERNAME, ""),
            CONF_PASSWORD: self._reauth_entry.data.get(CONF_PASSWORD, ""),
            CONF_CONTROLLER_DEVICE_ID: self._reauth_entry.data.get(
                CONF_CONTROLLER_DEVICE_ID,
                "",
            ),
        }

    async def _async_handle_user_step(
        self,
        step_id: str,
        user_input: dict[str, Any],
    ) -> FlowResult:
        """Validate B-hyve credentials from the initial or reauth flow."""

        errors: dict[str, str] = {}
        normalized_input = normalize_credentials(user_input)
        session = async_get_clientsession(self.hass)
        client = BhyveApiClient(
            normalized_input[CONF_USERNAME],
            normalized_input[CONF_PASSWORD],
            session,
        )

        try:
            await client.async_login()
            devices = await async_get_account_devices(client)
        except BhyveAuthenticationError:
            errors["base"] = "invalid_auth"
        except (BhyveApiError, ClientConnectionError, OSError):
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error while authenticating with B-hyve")
            errors["base"] = "unknown"
        else:
            controllers = discover_sprinkler_controllers(devices)
            if not controllers:
                errors["base"] = "no_controllers"
            else:
                controller_id = normalized_input.get(CONF_CONTROLLER_DEVICE_ID)
                if controller_id and all(
                    controller.mac != controller_id for controller in controllers
                ):
                    errors["base"] = "controller_not_found"
                else:
                    return await self._async_finish_login(normalized_input)

        schema = STEP_USER_DATA_SCHEMA
        if step_id == "reauth_confirm":
            schema = self.add_suggested_values_to_schema(schema, normalized_input)

        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=errors,
        )

    async def _async_finish_login(self, data: dict[str, Any]) -> FlowResult:
        """Create or update the config entry once authentication succeeds."""

        username = data[CONF_USERNAME]
        await self.async_set_unique_id(username)

        if self._reauth_entry is not None:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(
                self._reauth_entry,
                data_updates=data,
                reason="reauth_successful",
            )

        self._abort_if_unique_id_configured(updates=data)
        return self.async_create_entry(
            title="B-hyve Auto Sprinklers Controller",
            data=data,
        )
