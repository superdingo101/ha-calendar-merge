"""Config flow for Calendar Merge."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import CONF_CALENDAR_NAME, CONF_DEFAULT_CALENDAR, CONF_SOURCE_CALENDARS, DOMAIN

_LOGGER = logging.getLogger(__name__)


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return the schema for the user/options form."""
    defaults = defaults or {}

    # Default write calendar is optional – if omitted, create events are blocked
    # with a helpful error rather than silently discarded.
    default_calendar_default = defaults.get(CONF_DEFAULT_CALENDAR)
    default_calendar_field: Any = EntitySelector(
        EntitySelectorConfig(domain="calendar", multiple=False)
    )

    schema: dict[Any, Any] = {
        vol.Required(
            CONF_CALENDAR_NAME,
            default=defaults.get(CONF_CALENDAR_NAME, ""),
        ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
        vol.Required(
            CONF_SOURCE_CALENDARS,
            default=defaults.get(CONF_SOURCE_CALENDARS, []),
        ): EntitySelector(
            EntitySelectorConfig(domain="calendar", multiple=True)
        ),
    }

    # Optional field: use vol.Optional so the UI renders it clearly as optional
    if default_calendar_default:
        schema[vol.Optional(CONF_DEFAULT_CALENDAR, default=default_calendar_default)] = (
            default_calendar_field
        )
    else:
        schema[vol.Optional(CONF_DEFAULT_CALENDAR)] = default_calendar_field

    return vol.Schema(schema)


class CalendarMergeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Calendar Merge."""

    VERSION = 1

    # ------------------------------------------------------------------
    # User-initiated flow
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = self._validate(user_input)
            if not errors:
                title = user_input[CONF_CALENDAR_NAME]
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # YAML import flow
    # ------------------------------------------------------------------

    async def async_step_import(
        self, import_data: dict[str, Any]
    ) -> config_entries.FlowResult:
        """Import configuration from YAML (called by async_setup)."""
        # Avoid creating a duplicate entry for the same calendar name.
        for entry in self._async_current_entries():
            if entry.data.get(CONF_CALENDAR_NAME) == import_data.get(
                CONF_CALENDAR_NAME
            ):
                self.hass.config_entries.async_update_entry(entry, data=import_data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                _LOGGER.debug("Updated YAML import for '%s'.", import_data.get(CONF_CALENDAR_NAME))
                return self.async_abort(reason="already_configured")

        title = import_data[CONF_CALENDAR_NAME]
        return self.async_create_entry(title=title, data=import_data)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate(user_input: dict[str, Any]) -> dict[str, str]:
        errors: dict[str, str] = {}
        if not user_input.get(CONF_CALENDAR_NAME, "").strip():
            errors[CONF_CALENDAR_NAME] = "name_required"
        sources = user_input.get(CONF_SOURCE_CALENDARS) or []
        if not sources:
            errors[CONF_SOURCE_CALENDARS] = "no_sources"
        default = user_input.get(CONF_DEFAULT_CALENDAR)
        if default and default not in sources:
            errors[CONF_DEFAULT_CALENDAR] = "default_not_in_sources"
        return errors

    # ------------------------------------------------------------------
    # Options flow
    # ------------------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CalendarMergeOptionsFlow:
        return CalendarMergeOptionsFlow()


class CalendarMergeOptionsFlow(config_entries.OptionsFlow):
    """Handle options for an existing Calendar Merge entry."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        # Current values: options override data
        current = {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            errors = CalendarMergeConfigFlow._validate(user_input)
            if not errors:
                return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_user_schema(user_input or current),
            errors=errors,
        )
