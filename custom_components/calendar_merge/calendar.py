"""Calendar platform for Calendar Merge."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
)
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


def _normalize_when(value: "datetime | date") -> str:
    """
    Produce a stable, timezone-independent string from an event start value.

    Different calendar integrations return start times in different forms:
      - timezone-aware datetime (e.g. Google Calendar)
      - naive datetime (e.g. local CalDAV)
      - date object (all-day events)

    Without normalization, isoformat() produces different strings for the
    same logical moment, causing deduplication to silently fail.

    Strategy:
      - All-day events (date): use YYYY-MM-DD — no timezone ambiguity.
      - Timed events: convert to UTC, truncate to minute precision to absorb
        any second-level drift between sources.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = dt_util.as_local(value)
        utc = dt_util.as_utc(value)
        return utc.strftime("%Y-%m-%dT%H:%M")
    return value.isoformat()


def _dedup_key(event: CalendarEvent) -> str:
    """
    Return a stable deduplication key for an event.

    Key strategy:
      - normalized summary
      - normalized start
      - normalized end

    Summary is lowercased and stripped so minor capitalisation or whitespace
    differences between sources do not prevent matching.

    We intentionally do not use UID as the primary key because mirrored events
    from different calendar providers often have different UIDs while still
    representing the same real-world event.
    """
    summary = (event.summary or "").strip().lower()
    start_str = _normalize_when(event.start)
    end_str = _normalize_when(event.end)
    return f"summary_start_end\x00{summary}\x00{start_str}\x00{end_str}"


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


def _source_supports(entity: CalendarEntity, feature: CalendarEntityFeature) -> bool:
    """
    Return True if the source entity declares the given CalendarEntityFeature.

    This is the correct HA-idiomatic way to check write capability — inspect
    the entity's supported_features bitmask rather than poking at the MRO.
    """
    supported = entity.supported_features or 0
    return bool(supported & feature)


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------

class MergedCalendarEntity(CalendarEntity):
    """
    A virtual CalendarEntity that merges events from multiple source calendars.

    Read:
      Events are fetched from all sources, deduplicated (by UID, or summary
      + start time as fallback), and returned as a single sorted list.
      Duplicates get an annotation appended to their description listing
      every source calendar that contained them.

    Write (Update / Delete):
      Proxied to every source calendar that originally provided the event,
      identified via the internal _source_map (dedup_key → [entity_ids]).
      The annotation block is stripped from the description before forwarding.

    Write (Create):
      Routed to the configured default_calendar. Raises HomeAssistantError if
      none is configured or if that calendar does not support writes.

    Supported features are always advertised as CREATE | UPDATE | DELETE.
    At runtime, if a source calendar does not actually support a requested
    operation, a descriptive HomeAssistantError is raised instead.
    """

    # Always advertise all write features. We validate per-source at runtime.
    _attr_supported_features = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
    )
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
        # entity_id → event count or error string; updated on every fetch
        self._fetch_stats: dict[str, int | str] = {}

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
            "last_fetch_stats": self._fetch_stats,
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

        HA passes event fields as keyword arguments matching CalendarEvent fields
        (summary, dtstart, dtend, description, location, rrule, etc.).
        We forward them verbatim to the target source entity.
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

        if not _source_supports(entity, CalendarEntityFeature.CREATE_EVENT):
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
        event: dict[str, Any],
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """
        Update an existing event, proxied to every source calendar that owns it.

        HA passes the updated event as a plain dict of rfc5545 fields.
        We strip our merge annotation from the description field before forwarding.
        """
        sources = self._resolve_sources_for_uid(uid)
        if not sources:
            raise HomeAssistantError(
                f"Calendar Merge: could not find source calendar(s) for event "
                f"UID '{uid}'. Refresh the calendar view and try again."
            )

        # Strip our annotation from the description before sending upstream
        clean_event = dict(event)
        if "description" in clean_event:
            clean_event["description"] = _strip_merge_description(
                clean_event["description"]
            )

        errors: list[str] = []
        for entity_id in sources:
            entity = _get_calendar_entity(self.hass, entity_id)
            if entity is None:
                errors.append(f"{entity_id}: entity not found")
                continue
            if not _source_supports(entity, CalendarEntityFeature.UPDATE_EVENT):
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
            if not _source_supports(entity, CalendarEntityFeature.DELETE_EVENT):
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
        """Return source entity IDs that provided the event with the given UID."""
        key = f"uid\x00{uid}"
        if key in self._source_map:
            return self._source_map[key]
        # Fallback: partial match for events deduped by title+start
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
        seen: dict[str, tuple[CalendarEvent, list[str], set[str]]] = {}
        # Track per-source stats for diagnostics
        fetch_stats: dict[str, int | str] = {}

        for entity_id in self._source_entity_ids:
            entity = self._resolve_entity(entity_id)
            if entity is None:
                fetch_stats[entity_id] = "NOT FOUND"
                _LOGGER.warning(
                    "Calendar Merge (%s): source entity '%s' could not be resolved. "
                    "Check that the entity exists and is loaded.",
                    self._attr_name,
                    entity_id,
                )
                continue
            try:
                raw_events: list[CalendarEvent] = await entity.async_get_events(
                    self.hass, start_date, end_date
                )
                fetch_stats[entity_id] = len(raw_events)
                _LOGGER.debug(
                    "Calendar Merge (%s): fetched %d event(s) from '%s' for %s→%s",
                    self._attr_name,
                    len(raw_events),
                    entity_id,
                    start_date.date(),
                    end_date.date(),
                )
            except Exception:  # noqa: BLE001
                fetch_stats[entity_id] = "ERROR"
                _LOGGER.exception(
                    "Calendar Merge (%s): error fetching events from '%s'",
                    self._attr_name,
                    entity_id,
                )
                continue

            for event in raw_events:
                key = _dedup_key(event)
                if key in seen:
                    seen[key][1].append(entity_id)
                    if event.uid:
                        seen[key][2].add(event.uid)
                else:
                    seen[key] = (event, [entity_id], {event.uid} if event.uid else set())

        merged_events: list[CalendarEvent] = []
        source_map: dict[str, list[str]] = {}

        for key, (event, sources, uids) in seen.items():
            source_map[key] = sources
            for uid in uids:
                source_map[f"uid\x00{uid}"] = sources
            merged_events.append(_build_merged_event(event, sources))

        merged_events.sort(key=lambda e: _to_datetime(e.start))

        # Persist stats so they appear as entity attributes
        self._fetch_stats = fetch_stats

        _LOGGER.debug(
            "Calendar Merge (%s): merged to %d event(s). Per-source: %s",
            self._attr_name,
            len(merged_events),
            fetch_stats,
        )
        return merged_events, source_map

    def _resolve_entity(self, entity_id: str) -> "CalendarEntity | None":
        """
        Resolve a source CalendarEntity using multiple lookup strategies.

        HA 2024+ changed how entity components store entities internally.
        We try the EntityComponent approach first, then fall back to
        iterating over all calendar platforms registered in hass.data,
        to handle cases where get_entity() returns None despite the entity
        being alive and registered.
        """
        # Strategy 1: EntityComponent lookup (works in most HA versions)
        calendar_component = self.hass.data.get(CALENDAR_DOMAIN)
        if calendar_component is not None:
            entity = calendar_component.get_entity(entity_id)
            if entity is not None and isinstance(entity, CalendarEntity):
                return entity

        # Strategy 2: Iterate registered entity platforms for this domain.
        # In HA 2024+ each platform registers itself under
        # hass.data["entity_components"][CALENDAR_DOMAIN] or via platform list.
        for key, value in self.hass.data.items():
            if key == DOMAIN:
                # Skip our own data
                continue
            if hasattr(value, "get_entity"):
                try:
                    entity = value.get_entity(entity_id)
                    if entity is not None and isinstance(entity, CalendarEntity):
                        _LOGGER.debug(
                            "Resolved '%s' via fallback lookup key '%s'",
                            entity_id, key,
                        )
                        return entity
                except Exception:  # noqa: BLE001
                    pass

        return None
