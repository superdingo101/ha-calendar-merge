"""Calendar platform for Calendar Merge."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.components.calendar import DOMAIN as CALENDAR_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CALENDAR_NAME,
    CONF_DEFAULT_CALENDAR,
    CONF_SOURCE_CALENDARS,
    DOMAIN,
    LOOKAHEAD_HOURS,
    MERGE_BLOCK_SENTINEL,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=15)


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the merged calendar entity from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    name: str = data.get(CONF_CALENDAR_NAME, entry.title)
    source_ids: list[str] = data.get(CONF_SOURCE_CALENDARS, [])
    default_calendar: str | None = data.get(CONF_DEFAULT_CALENDAR)

    async_add_entities(
        [MergedCalendarEntity(hass, entry.entry_id, name, source_ids, default_calendar)],
        update_before_add=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_datetime(value: datetime | date) -> datetime:
    """Convert a date or datetime to a timezone-aware datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return dt_util.as_local(value)
        return value
    return dt_util.start_of_local_day(
        datetime(value.year, value.month, value.day)
    )


def _dedup_key(event: CalendarEvent) -> str:
    """
    Return a stable deduplication key for an event.

    Priority:
      1. UID (most reliable – set by calendar servers)
      2. summary + ISO start string (fallback for caldav-less calendars)
    """
    if event.uid:
        return f"uid\x00{event.uid}"
    start_str = (
        event.start.isoformat()
        if hasattr(event.start, "isoformat")
        else str(event.start)
    )
    return f"title_start\x00{event.summary}\x00{start_str}"


def _strip_merge_description(description: str | None) -> str | None:
    """
    Remove the Calendar Merge annotation block from a description before
    proxying an update to source calendars, so the original event is not
    polluted with our metadata.
    """
    if not description:
        return description
    idx = description.find(MERGE_BLOCK_SENTINEL)
    if idx == -1:
        return description
    stripped = description[:idx]
    return stripped if stripped else None


def _build_merged_event(
    base_event: CalendarEvent,
    sources: list[str],
) -> CalendarEvent:
    """
    Return a new CalendarEvent with source-calendar information appended.

    Single source  → description unchanged.
    Multiple sources → appends an annotation block listing all source calendars.
    """
    description = base_event.description or ""

    if len(sources) > 1:
        source_block = (
            MERGE_BLOCK_SENTINEL
            + "This event appears in multiple calendars:\n"
            + "\n".join(f"  • {s}" for s in sources)
        )
        description = description + source_block

    return CalendarEvent(
        start=base_event.start,
        end=base_event.end,
        summary=base_event.summary,
        description=description if description else None,
        location=base_event.location,
        uid=base_event.uid,
        recurrence_id=getattr(base_event, "recurrence_id", None),
        rrule=getattr(base_event, "rrule", None),
    )


def _get_calendar_entity(
    hass: HomeAssistant, entity_id: str
) -> CalendarEntity | None:
    """Resolve a CalendarEntity from the platform registry."""
    calendar_component = hass.data.get(CALENDAR_DOMAIN)
    if calendar_component is None:
        _LOGGER.warning("Calendar component not loaded; cannot resolve %s", entity_id)
        return None
    entity = calendar_component.get_entity(entity_id)
    if entity is None:
        _LOGGER.warning("Calendar entity not found: %s", entity_id)
        return None
    if not isinstance(entity, CalendarEntity):
        _LOGGER.warning("%s is not a CalendarEntity", entity_id)
        return None
    return entity


def _supports_write(entity: CalendarEntity, method: str) -> bool:
    """
    Return True if the entity's concrete class has overridden the given write
    method (meaning it supports that write operation).
    """
    for cls in type(entity).__mro__:
        if cls is CalendarEntity:
            # Reached the base class without finding an override
            return False
        if method in cls.__dict__:
            return True
    return False


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------

class MergedCalendarEntity(CalendarEntity):
    """
    A virtual CalendarEntity that merges events from multiple source calendars.

    Read:
      Events are fetched from all sources, deduplicated (by UID, or summary
      + start time as fallback), and returned as a single sorted list.
      Duplicates get an annotation appended to their description that lists
      every source calendar that contained them.

    Write (Update / Delete):
      Proxied to every source calendar that originally provided the event,
      identified via the internal _source_map (dedup_key → [entity_ids]).
      The annotation block is stripped from the description before forwarding.

    Write (Create):
      Routed to the configured default_calendar. Raises HomeAssistantError if
      none is configured or if that calendar does not support writes.
    """

    _attr_should_poll = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        name: str,
        source_entity_ids: list[str],
        default_calendar: str | None,
    ) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self._attr_name = name
        self._source_entity_ids = source_entity_ids
        self._default_calendar = default_calendar
        self._attr_unique_id = entry_id
        self._event: CalendarEvent | None = None
        # dedup_key → list[source_entity_id]; kept in sync on every fetch
        self._source_map: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # Read interface
    # ------------------------------------------------------------------

    @property
    def event(self) -> CalendarEvent | None:
        return self._event

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        duplicates = {
            key: sources
            for key, sources in self._source_map.items()
            if len(sources) > 1
        }
        return {
            "source_calendars": self._source_entity_ids,
            "default_calendar": self._default_calendar,
            "duplicate_events": duplicates,
        }

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        events, source_map = await self._fetch_and_merge(start_date, end_date)
        self._source_map = source_map
        return events

    async def async_update(self) -> None:
        now = dt_util.now()
        end = now + timedelta(hours=LOOKAHEAD_HOURS)
        events, source_map = await self._fetch_and_merge(now, end)
        self._source_map = source_map
        self._event = None
        for ev in sorted(events, key=lambda e: _to_datetime(e.start)):
            if _to_datetime(ev.end) > now:
                self._event = ev
                break

    # ------------------------------------------------------------------
    # Write interface
    # ------------------------------------------------------------------

    async def async_create_event(self, **kwargs: Any) -> None:
        """
        Create a new event on the configured default calendar.

        Raises HomeAssistantError if:
          - No default calendar is configured.
          - The default calendar entity cannot be found.
          - The default calendar does not support creating events.
        """
        if not self._default_calendar:
            raise HomeAssistantError(
                "Calendar Merge: no default calendar is configured for new events. "
                "Go to Settings → Integrations → Calendar Merge → Configure to set one."
            )

        entity = _get_calendar_entity(self.hass, self._default_calendar)
        if entity is None:
            raise HomeAssistantError(
                f"Calendar Merge: default calendar '{self._default_calendar}' not found."
            )

        if not _supports_write(entity, "async_create_event"):
            raise HomeAssistantError(
                f"Calendar Merge: '{self._default_calendar}' does not support "
                "creating events (it may be read-only)."
            )

        _LOGGER.debug(
            "Creating event '%s' on default calendar '%s'",
            kwargs.get("summary", "<no title>"),
            self._default_calendar,
        )
        await entity.async_create_event(**kwargs)

    async def async_update_event(
        self,
        uid: str,
        event: CalendarEvent,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """
        Update an existing event, proxied to every source calendar that owns it.

        The Calendar Merge annotation is stripped from the description before
        forwarding, so the source events are not polluted with our metadata.
        """
        sources = self._resolve_sources_for_uid(uid)
        if not sources:
            raise HomeAssistantError(
                f"Calendar Merge: could not find source calendar(s) for event "
                f"UID '{uid}'. Refresh the calendar view and try again."
            )

        # Strip our annotation from the description before sending upstream
        clean_event = CalendarEvent(
            start=event.start,
            end=event.end,
            summary=event.summary,
            description=_strip_merge_description(event.description),
            location=event.location,
            uid=event.uid,
            recurrence_id=getattr(event, "recurrence_id", None),
            rrule=getattr(event, "rrule", None),
        )

        errors: list[str] = []
        for entity_id in sources:
            entity = _get_calendar_entity(self.hass, entity_id)
            if entity is None:
                errors.append(f"{entity_id}: entity not found")
                continue
            if not _supports_write(entity, "async_update_event"):
                errors.append(f"{entity_id}: does not support updating events (read-only)")
                continue
            try:
                _LOGGER.debug("Proxying update uid='%s' → '%s'", uid, entity_id)
                await entity.async_update_event(
                    uid,
                    clean_event,
                    recurrence_id=recurrence_id,
                    recurrence_range=recurrence_range,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{entity_id}: {exc}")
                _LOGGER.exception("Error updating event on %s", entity_id)

        if errors:
            raise HomeAssistantError(
                "Calendar Merge: the following source calendar(s) failed to update:\n"
                + "\n".join(f"  • {e}" for e in errors)
            )

        await self.async_update()

    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """
        Delete an event, proxied to every source calendar that owns it.
        """
        sources = self._resolve_sources_for_uid(uid)
        if not sources:
            raise HomeAssistantError(
                f"Calendar Merge: could not find source calendar(s) for event "
                f"UID '{uid}'. Refresh the calendar view and try again."
            )

        errors: list[str] = []
        for entity_id in sources:
            entity = _get_calendar_entity(self.hass, entity_id)
            if entity is None:
                errors.append(f"{entity_id}: entity not found")
                continue
            if not _supports_write(entity, "async_delete_event"):
                errors.append(f"{entity_id}: does not support deleting events (read-only)")
                continue
            try:
                _LOGGER.debug("Proxying delete uid='%s' → '%s'", uid, entity_id)
                await entity.async_delete_event(
                    uid,
                    recurrence_id=recurrence_id,
                    recurrence_range=recurrence_range,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{entity_id}: {exc}")
                _LOGGER.exception("Error deleting event on %s", entity_id)

        if errors:
            raise HomeAssistantError(
                "Calendar Merge: the following source calendar(s) failed to delete:\n"
                + "\n".join(f"  • {e}" for e in errors)
            )

        await self.async_update()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_sources_for_uid(self, uid: str) -> list[str]:
        """
        Return the source entity IDs that provided the event with the given UID.

        Checks the uid-prefixed key first (fastest path), then falls back to
        a substring scan for events that were deduped by title+start but whose
        UID is referenced in the map key.
        """
        key = f"uid\x00{uid}"
        if key in self._source_map:
            return self._source_map[key]
        # Fallback: partial match in case key format differs
        if uid:
            for map_key, sources in self._source_map.items():
                if uid in map_key:
                    return sources
        return []

    async def _fetch_and_merge(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> tuple[list[CalendarEvent], dict[str, list[str]]]:
        """
        Fetch events from all source calendars and apply deduplication.

        Returns (merged_event_list, source_map).
        source_map maps dedup_key → [source_entity_ids].
        """
        seen: dict[str, tuple[CalendarEvent, list[str]]] = {}

        calendar_component = self.hass.data.get(CALENDAR_DOMAIN)
        if calendar_component is None:
            _LOGGER.warning("Calendar component not available; cannot fetch source events.")
            return [], {}

        for entity_id in self._source_entity_ids:
            entity = calendar_component.get_entity(entity_id)
            if entity is None:
                _LOGGER.debug("Source calendar entity not found: %s", entity_id)
                continue
            try:
                raw_events: list[CalendarEvent] = await entity.async_get_events(
                    self.hass, start_date, end_date
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Error fetching events from %s", entity_id)
                continue

            for event in raw_events:
                key = _dedup_key(event)
                if key in seen:
                    seen[key][1].append(entity_id)
                else:
                    seen[key] = (event, [entity_id])

        merged_events: list[CalendarEvent] = []
        source_map: dict[str, list[str]] = {}

        for key, (event, sources) in seen.items():
            source_map[key] = sources
            merged_events.append(_build_merged_event(event, sources))

        merged_events.sort(key=lambda e: _to_datetime(e.start))
        return merged_events, source_map
