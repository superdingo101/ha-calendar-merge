"""Calendar Merge integration."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_CALENDAR_NAME,
    CONF_DEFAULT_CALENDAR,
    CONF_SOURCE_CALENDARS,
    DOMAIN,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# YAML configuration schema
# ---------------------------------------------------------------------------
def _validate_yaml_entry(entry: dict) -> dict:
    """Validate invariants for a YAML-configured merge entry."""
    sources = entry.get(CONF_SOURCE_CALENDARS) or []
    default = entry.get(CONF_DEFAULT_CALENDAR)
    if default and default not in sources:
        raise vol.Invalid(
            f"{CONF_DEFAULT_CALENDAR} must be one of {CONF_SOURCE_CALENDARS}"
        )
    return entry


_ENTRY_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(CONF_CALENDAR_NAME): cv.string,
            vol.Required(CONF_SOURCE_CALENDARS): vol.All(
                cv.ensure_list, [cv.entity_id]
            ),
            vol.Optional(CONF_DEFAULT_CALENDAR): cv.entity_id,
        }
    ),
    _validate_yaml_entry,
)

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.All(cv.ensure_list, [_ENTRY_SCHEMA])},
    extra=vol.ALLOW_EXTRA,
)


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Import YAML-configured entries into the config-entry system."""
    hass.data.setdefault(DOMAIN, {})

    if DOMAIN not in config:
        return True

    for entry_config in config[DOMAIN]:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data=dict(entry_config),
            )
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Calendar Merge from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Merge options over data so options-flow changes take effect without
    # requiring a full re-import.
    merged_data = {**entry.data, **entry.options}
    hass.data[DOMAIN][entry.entry_id] = merged_data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry when options are updated so the entity reflects changes.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
