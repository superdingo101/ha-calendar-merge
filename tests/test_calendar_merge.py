from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import IntFlag
import importlib.util
from itertools import combinations
from pathlib import Path
import sys
import types
from typing import Any

import pytest


class CalendarEntityFeature(IntFlag):
    CREATE_EVENT = 1
    DELETE_EVENT = 2
    UPDATE_EVENT = 4


@dataclass
class CalendarEvent:
    start: datetime | date
    end: datetime | date
    summary: str | None = None
    description: str | None = None
    location: str | None = None
    uid: str | None = None
    recurrence_id: str | None = None
    rrule: str | None = None


class CalendarEntity:
    supported_features: int = 0


class HomeAssistantError(Exception):
    pass


class HomeAssistant:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.services: Any = None


class FakeCalendarComponent:
    def __init__(self, entities: dict[str, CalendarEntity]) -> None:
        self._entities = entities

    def get_entity(self, entity_id: str):
        return self._entities.get(entity_id)


class FakeSourceCalendar(CalendarEntity):
    def __init__(
        self,
        events: list[CalendarEvent],
        features: CalendarEntityFeature,
        should_raise_on_update: bool = False,
        should_raise_on_delete: bool = False,
        created_event_uid: str | None = None,
    ) -> None:
        self._events = events
        self._pending_events: list[CalendarEvent] = []
        self.supported_features = int(features)
        self.should_raise_on_update = should_raise_on_update
        self.should_raise_on_delete = should_raise_on_delete
        self.created_event_uid = created_event_uid
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.deleted: list[dict[str, Any]] = []
        self.update_calls = 0

    async def async_get_events(
        self, hass: Any, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return self._events

    async def async_create_event(self, **kwargs: Any) -> None:
        self.created.append(kwargs)
        start = kwargs.get("start_date_time", kwargs.get("start_date"))
        end = kwargs.get("end_date_time", kwargs.get("end_date"))
        if start is None or end is None:
            return

        uid = kwargs.get("uid", self.created_event_uid)
        self._pending_events.append(
            CalendarEvent(
                start=start,
                end=end,
                summary=kwargs.get("summary"),
                description=kwargs.get("description"),
                location=kwargs.get("location"),
                uid=uid,
            )
        )

    async def async_update(self) -> None:
        self.update_calls += 1
        if self._pending_events:
            self._events.extend(self._pending_events)
            self._pending_events = []

    async def async_update_event(
        self,
        uid: str,
        event: dict[str, Any],
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        if self.should_raise_on_update:
            raise RuntimeError("boom-update")
        self.updated.append(
            {
                "uid": uid,
                "event": event,
                "recurrence_id": recurrence_id,
                "recurrence_range": recurrence_range,
            }
        )

    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        if self.should_raise_on_delete:
            raise RuntimeError("boom-delete")
        self.deleted.append(
            {
                "uid": uid,
                "recurrence_id": recurrence_id,
                "recurrence_range": recurrence_range,
            }
        )


class FakeServices:
    def __init__(self, entities: dict[str, CalendarEntity]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._entities = entities

    async def async_call(
        self,
        domain: str,
        service: str,
        data: dict[str, Any],
        blocking: bool = False,
    ) -> None:
        self.calls.append(
            {
                "domain": domain,
                "service": service,
                "data": data,
                "blocking": blocking,
            }
        )
        if domain == "homeassistant" and service == "update_entity":
            entity_id = data["entity_id"]
            entity = self._entities[entity_id]
            if hasattr(entity, "async_update"):
                await entity.async_update()


@pytest.fixture
def calendar_module(monkeypatch: pytest.MonkeyPatch):
    calendar_mod = types.ModuleType("homeassistant.components.calendar")
    calendar_mod.CalendarEntity = CalendarEntity
    calendar_mod.CalendarEntityFeature = CalendarEntityFeature
    calendar_mod.CalendarEvent = CalendarEvent
    calendar_mod.DOMAIN = "calendar"

    config_entries_mod = types.ModuleType("homeassistant.config_entries")
    config_entries_mod.ConfigEntry = object

    core_mod = types.ModuleType("homeassistant.core")
    core_mod.HomeAssistant = HomeAssistant

    exceptions_mod = types.ModuleType("homeassistant.exceptions")
    exceptions_mod.HomeAssistantError = HomeAssistantError

    entity_platform_mod = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform_mod.AddEntitiesCallback = object

    dt_mod = types.ModuleType("homeassistant.util.dt")
    dt_mod.as_local = (
        lambda value: value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value
    )
    dt_mod.as_utc = lambda value: value.astimezone(timezone.utc)
    dt_mod.start_of_local_day = lambda value: value.replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
    )
    dt_mod.now = lambda: datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc)

    util_mod = types.ModuleType("homeassistant.util")
    util_mod.dt = dt_mod

    root = Path(__file__).resolve().parents[1]
    monkeypatch.setitem(sys.modules, "homeassistant", types.ModuleType("homeassistant"))
    monkeypatch.setitem(
        sys.modules, "homeassistant.components", types.ModuleType("homeassistant.components")
    )
    monkeypatch.setitem(sys.modules, "homeassistant.components.calendar", calendar_mod)
    monkeypatch.setitem(sys.modules, "homeassistant.config_entries", config_entries_mod)
    monkeypatch.setitem(sys.modules, "homeassistant.core", core_mod)
    monkeypatch.setitem(sys.modules, "homeassistant.exceptions", exceptions_mod)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.entity_platform", entity_platform_mod)
    monkeypatch.setitem(sys.modules, "homeassistant.util", util_mod)
    monkeypatch.setitem(sys.modules, "homeassistant.util.dt", dt_mod)

    custom_components_pkg = types.ModuleType("custom_components")
    custom_components_pkg.__path__ = [str(root / "custom_components")]
    merge_pkg = types.ModuleType("custom_components.calendar_merge")
    merge_pkg.__path__ = [str(root / "custom_components" / "calendar_merge")]
    monkeypatch.setitem(sys.modules, "custom_components", custom_components_pkg)
    monkeypatch.setitem(sys.modules, "custom_components.calendar_merge", merge_pkg)

    const_spec = importlib.util.spec_from_file_location(
        "custom_components.calendar_merge.const",
        root / "custom_components" / "calendar_merge" / "const.py",
    )
    const_module = importlib.util.module_from_spec(const_spec)
    sys.modules[const_spec.name] = const_module
    const_spec.loader.exec_module(const_module)

    calendar_spec = importlib.util.spec_from_file_location(
        "custom_components.calendar_merge.calendar",
        root / "custom_components" / "calendar_merge" / "calendar.py",
    )
    module = importlib.util.module_from_spec(calendar_spec)
    sys.modules[calendar_spec.name] = module
    calendar_spec.loader.exec_module(module)
    return module


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _all_non_empty_source_combinations() -> list[tuple[str, ...]]:
    source_ids = (
        "calendar.google_work",
        "calendar.apple_family",
        "calendar.homeassistant",
    )
    combos: list[tuple[str, ...]] = []
    for size in (1, 2, 3):
        combos.extend(combinations(source_ids, size))
    return combos


def test_merge_deduplicates_by_uid_across_google_caldav_and_native(calendar_module):
    shared_uid = "dup-uid-1"
    entities = {
        "calendar.google_work": FakeSourceCalendar(
            [
                CalendarEvent(
                    uid=shared_uid,
                    summary="Team Standup",
                    start=datetime(2024, 1, 2, 9, 0, tzinfo=timezone.utc),
                    end=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
                    description="google",
                )
            ],
            CalendarEntityFeature.CREATE_EVENT
            | CalendarEntityFeature.UPDATE_EVENT
            | CalendarEntityFeature.DELETE_EVENT,
        ),
        "calendar.apple_family": FakeSourceCalendar(
            [
                CalendarEvent(
                    uid=shared_uid,
                    summary="Team Standup",
                    start=datetime(2024, 1, 2, 9, 0),
                    end=datetime(2024, 1, 2, 9, 30),
                    description="caldav",
                )
            ],
            CalendarEntityFeature.CREATE_EVENT
            | CalendarEntityFeature.UPDATE_EVENT
            | CalendarEntityFeature.DELETE_EVENT,
        ),
        "calendar.homeassistant": FakeSourceCalendar(
            [
                CalendarEvent(
                    uid=shared_uid,
                    summary="Team Standup",
                    start=datetime(2024, 1, 2, 9, 0, tzinfo=timezone.utc),
                    end=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
                    description="native",
                )
            ],
            CalendarEntityFeature.CREATE_EVENT
            | CalendarEntityFeature.UPDATE_EVENT
            | CalendarEntityFeature.DELETE_EVENT,
        ),
    }
    hass = HomeAssistant()
    hass.data[calendar_module.CALENDAR_DOMAIN] = FakeCalendarComponent(entities)
    merged = calendar_module.MergedCalendarEntity(
        hass,
        "entry-1",
        "Merged",
        list(entities.keys()),
        "calendar.google_work",
    )

    events = _run(
        merged.async_get_events(
            hass,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 3, tzinfo=timezone.utc),
        )
    )

    assert len(events) == 1
    description = events[0].description or ""
    assert "calendar.google_work" in description
    assert "calendar.apple_family" in description
    assert "calendar.homeassistant" in description


def test_merge_deduplicates_same_event_with_different_uids(calendar_module):
    entities = {
        "calendar.google_work": FakeSourceCalendar(
            [
                CalendarEvent(
                    uid="google-abc",
                    summary="Soccer Practice",
                    start=datetime(2024, 3, 12, 17, 0, tzinfo=timezone.utc),
                    end=datetime(2024, 3, 12, 18, 0, tzinfo=timezone.utc),
                )
            ],
            CalendarEntityFeature.CREATE_EVENT
            | CalendarEntityFeature.UPDATE_EVENT
            | CalendarEntityFeature.DELETE_EVENT,
        ),
        "calendar.apple_family": FakeSourceCalendar(
            [
                CalendarEvent(
                    uid="caldav-xyz",
                    summary="Soccer Practice",
                    start=datetime(2024, 3, 12, 17, 0),
                    end=datetime(2024, 3, 12, 18, 0),
                )
            ],
            CalendarEntityFeature.CREATE_EVENT
            | CalendarEntityFeature.UPDATE_EVENT
            | CalendarEntityFeature.DELETE_EVENT,
        ),
    }
    hass = HomeAssistant()
    hass.data[calendar_module.CALENDAR_DOMAIN] = FakeCalendarComponent(entities)
    merged = calendar_module.MergedCalendarEntity(
        hass,
        "entry-uids",
        "Merged",
        list(entities.keys()),
        "calendar.google_work",
    )

    events = _run(
        merged.async_get_events(
            hass,
            datetime(2024, 3, 1, tzinfo=timezone.utc),
            datetime(2024, 3, 30, tzinfo=timezone.utc),
        )
    )

    assert len(events) == 1
    assert merged._source_map["uid\x00google-abc"] == [
        "calendar.google_work",
        "calendar.apple_family",
    ]
    assert merged._source_map["uid\x00caldav-xyz"] == [
        "calendar.google_work",
        "calendar.apple_family",
    ]


def test_merge_deduplicates_by_title_and_start_when_uid_missing(calendar_module):
    start_naive = datetime(2024, 2, 10, 14, 0)
    start_aware = datetime(2024, 2, 10, 14, 0, tzinfo=timezone.utc)
    entities = {
        "calendar.google_work": FakeSourceCalendar(
            [
                CalendarEvent(
                    summary="No UID Event",
                    start=start_aware,
                    end=datetime(2024, 2, 10, 15, 0, tzinfo=timezone.utc),
                    uid=None,
                )
            ],
            CalendarEntityFeature.CREATE_EVENT
            | CalendarEntityFeature.UPDATE_EVENT
            | CalendarEntityFeature.DELETE_EVENT,
        ),
        "calendar.apple_family": FakeSourceCalendar(
            [
                CalendarEvent(
                    summary="  no uid event  ",
                    start=start_naive,
                    end=datetime(2024, 2, 10, 15, 0),
                    uid=None,
                )
            ],
            CalendarEntityFeature.CREATE_EVENT
            | CalendarEntityFeature.UPDATE_EVENT
            | CalendarEntityFeature.DELETE_EVENT,
        ),
    }

    hass = HomeAssistant()
    hass.data[calendar_module.CALENDAR_DOMAIN] = FakeCalendarComponent(entities)
    merged = calendar_module.MergedCalendarEntity(
        hass,
        "entry-2",
        "Merged",
        list(entities.keys()),
        "calendar.google_work",
    )

    events = _run(
        merged.async_get_events(
            hass,
            datetime(2024, 2, 1, tzinfo=timezone.utc),
            datetime(2024, 2, 28, tzinfo=timezone.utc),
        )
    )

    assert len(events) == 1


def test_merge_deduplicates_all_day_date_events_without_uid(calendar_module):
    event_date = date(2024, 3, 5)
    entities = {
        "calendar.google_work": FakeSourceCalendar(
            [
                CalendarEvent(
                    summary="Holiday",
                    start=event_date,
                    end=event_date,
                    uid=None,
                )
            ],
            CalendarEntityFeature.CREATE_EVENT
            | CalendarEntityFeature.UPDATE_EVENT
            | CalendarEntityFeature.DELETE_EVENT,
        ),
        "calendar.homeassistant": FakeSourceCalendar(
            [
                CalendarEvent(
                    summary="holiday",
                    start=event_date,
                    end=event_date,
                    uid=None,
                )
            ],
            CalendarEntityFeature.CREATE_EVENT
            | CalendarEntityFeature.UPDATE_EVENT
            | CalendarEntityFeature.DELETE_EVENT,
        ),
    }

    hass = HomeAssistant()
    hass.data[calendar_module.CALENDAR_DOMAIN] = FakeCalendarComponent(entities)
    merged = calendar_module.MergedCalendarEntity(
        hass,
        "entry-3",
        "Merged",
        list(entities.keys()),
        "calendar.google_work",
    )

    events = _run(
        merged.async_get_events(
            hass,
            datetime(2024, 3, 1, tzinfo=timezone.utc),
            datetime(2024, 3, 30, tzinfo=timezone.utc),
        )
    )

    assert len(events) == 1


@pytest.mark.parametrize("target", [
    "calendar.google_work",
    "calendar.apple_family",
    "calendar.homeassistant",
])
def test_create_routes_to_default_calendar_for_each_calendar_type(calendar_module, target):
    full = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
    )
    entities = {
        "calendar.google_work": FakeSourceCalendar([], full),
        "calendar.apple_family": FakeSourceCalendar([], full),
        "calendar.homeassistant": FakeSourceCalendar([], full),
    }
    hass = HomeAssistant()
    hass.data[calendar_module.CALENDAR_DOMAIN] = FakeCalendarComponent(entities)
    hass.services = FakeServices(entities)
    merged = calendar_module.MergedCalendarEntity(
        hass,
        "entry-4",
        "Merged",
        list(entities.keys()),
        target,
    )

    _run(
        merged.async_create_event(
            summary="Created",
            start_date_time=datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc),
            end_date_time=datetime(2024, 1, 2, 11, 0, tzinfo=timezone.utc),
        )
    )

    for entity_id, source in entities.items():
        if entity_id == target:
            assert source.created == [
                {
                    "summary": "Created",
                    "start_date_time": datetime(
                        2024, 1, 2, 10, 0, tzinfo=timezone.utc
                    ),
                    "end_date_time": datetime(
                        2024, 1, 2, 11, 0, tzinfo=timezone.utc
                    ),
                }
            ]
        else:
            assert source.created == []


def test_create_raises_without_default_calendar(calendar_module):
    hass = HomeAssistant()
    hass.data[calendar_module.CALENDAR_DOMAIN] = FakeCalendarComponent({})
    hass.services = FakeServices({})
    merged = calendar_module.MergedCalendarEntity(
        hass,
        "entry-5",
        "Merged",
        [],
        None,
    )

    with pytest.raises(HomeAssistantError, match="no default calendar"):
        _run(merged.async_create_event(summary="bad"))


def test_create_raises_when_default_calendar_missing(calendar_module):
    hass = HomeAssistant()
    hass.data[calendar_module.CALENDAR_DOMAIN] = FakeCalendarComponent({})
    hass.services = FakeServices({})
    merged = calendar_module.MergedCalendarEntity(
        hass,
        "entry-6",
        "Merged",
        [],
        "calendar.google_work",
    )

    with pytest.raises(HomeAssistantError, match="not found"):
        _run(merged.async_create_event(summary="bad"))


def test_create_raises_when_default_calendar_is_read_only(calendar_module):
    entities = {
        "calendar.google_work": FakeSourceCalendar([], CalendarEntityFeature.UPDATE_EVENT)
    }
    hass = HomeAssistant()
    hass.data[calendar_module.CALENDAR_DOMAIN] = FakeCalendarComponent(entities)
    hass.services = FakeServices(entities)
    merged = calendar_module.MergedCalendarEntity(
        hass,
        "entry-7",
        "Merged",
        ["calendar.google_work"],
        "calendar.google_work",
    )

    with pytest.raises(HomeAssistantError, match="does not support creating"):
        _run(merged.async_create_event(summary="bad"))


def test_create_refreshes_default_calendar_and_seeds_source_map(calendar_module):
    full = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
    )
    entities = {
        "calendar.google_work": FakeSourceCalendar(
            [], full, created_event_uid="google-created-uid"
        ),
        "calendar.apple_family": FakeSourceCalendar([], full),
    }
    hass = HomeAssistant()
    hass.data[calendar_module.CALENDAR_DOMAIN] = FakeCalendarComponent(entities)
    hass.services = FakeServices(entities)
    merged = calendar_module.MergedCalendarEntity(
        hass,
        "entry-7b",
        "Merged",
        list(entities.keys()),
        "calendar.google_work",
    )

    _run(
        merged.async_create_event(
            summary="Created",
            start_date_time=datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc),
            end_date_time=datetime(2024, 1, 2, 11, 0, tzinfo=timezone.utc),
        )
    )

    assert hass.services.calls == [
        {
            "domain": "homeassistant",
            "service": "update_entity",
            "data": {"entity_id": "calendar.google_work"},
            "blocking": True,
        }
    ]
    assert entities["calendar.google_work"].update_calls == 1
    assert merged._resolve_sources_for_uid("google-created-uid") == [
        "calendar.google_work"
    ]


def test_delete_after_create_uses_seeded_uid_mapping(calendar_module):
    full = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
    )
    entities = {
        "calendar.google_work": FakeSourceCalendar(
            [], full, created_event_uid="google-created-uid"
        ),
        "calendar.apple_family": FakeSourceCalendar([], full),
    }
    hass = HomeAssistant()
    hass.data[calendar_module.CALENDAR_DOMAIN] = FakeCalendarComponent(entities)
    hass.services = FakeServices(entities)
    merged = calendar_module.MergedCalendarEntity(
        hass,
        "entry-7c",
        "Merged",
        list(entities.keys()),
        "calendar.google_work",
    )

    _run(
        merged.async_create_event(
            summary="Created",
            start_date_time=datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc),
            end_date_time=datetime(2024, 1, 2, 11, 0, tzinfo=timezone.utc),
        )
    )
    _run(merged.async_delete_event("google-created-uid"))

    assert entities["calendar.google_work"].deleted == [
        {
            "uid": "google-created-uid",
            "recurrence_id": None,
            "recurrence_range": None,
        }
    ]
    assert entities["calendar.apple_family"].deleted == []


def test_seed_source_map_for_created_event_preserves_existing_owners(calendar_module):
    full = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
    )
    created_start = datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc)
    created_end = datetime(2024, 1, 2, 11, 0, tzinfo=timezone.utc)
    entities = {
        "calendar.google_work": FakeSourceCalendar(
            [
                CalendarEvent(
                    start=created_start,
                    end=created_end,
                    summary="Created",
                    uid="google-existing-uid",
                )
            ],
            full,
            created_event_uid="google-created-uid",
        ),
        "calendar.apple_family": FakeSourceCalendar([], full),
    }
    hass = HomeAssistant()
    hass.data[calendar_module.CALENDAR_DOMAIN] = FakeCalendarComponent(entities)
    hass.services = FakeServices(entities)
    merged = calendar_module.MergedCalendarEntity(
        hass,
        "entry-7d",
        "Merged",
        list(entities.keys()),
        "calendar.google_work",
    )
    created_key = "summary_start_end\x00created\x002024-01-02T10:00\x002024-01-02T11:00"
    merged._source_map = {
        created_key: ["calendar.apple_family"],
        "uid\x00apple-shared-uid": ["calendar.apple_family"],
    }

    created_event = CalendarEvent(
        start=created_start,
        end=created_end,
        summary="Created",
    )

    _run(
        merged._async_seed_source_map_for_created_event(
            entities["calendar.google_work"],
            "calendar.google_work",
            created_event,
        )
    )

    assert merged._source_map[created_key] == [
        "calendar.apple_family",
        "calendar.google_work",
    ]
    assert merged._source_map["uid\x00google-existing-uid"] == [
        "calendar.apple_family",
        "calendar.google_work",
    ]


@pytest.mark.parametrize("owners", _all_non_empty_source_combinations())
def test_update_proxies_to_all_owner_combinations(calendar_module, owners):
    full = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
    )
    entities = {
        "calendar.google_work": FakeSourceCalendar([], full),
        "calendar.apple_family": FakeSourceCalendar([], full),
        "calendar.homeassistant": FakeSourceCalendar([], full),
    }

    hass = HomeAssistant()
    hass.data[calendar_module.CALENDAR_DOMAIN] = FakeCalendarComponent(entities)
    merged = calendar_module.MergedCalendarEntity(
        hass,
        "entry-8",
        "Merged",
        list(entities.keys()),
        "calendar.google_work",
    )
    merged._source_map = {"uid\x00event-123": list(owners)}

    payload = {
        "summary": "Changed",
        "description": "Original"
        + calendar_module.MERGE_BLOCK_SENTINEL
        + "This event appears in multiple calendars",
    }

    _run(merged.async_update_event("event-123", payload))

    for entity_id, source in entities.items():
        if entity_id in owners:
            assert len(source.updated) == 1
            assert source.updated[0]["uid"] == "event-123"
            assert source.updated[0]["event"]["description"] == "Original"
        else:
            assert source.updated == []


@pytest.mark.parametrize("owners", _all_non_empty_source_combinations())
def test_delete_proxies_to_all_owner_combinations(calendar_module, owners):
    full = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
    )
    entities = {
        "calendar.google_work": FakeSourceCalendar([], full),
        "calendar.apple_family": FakeSourceCalendar([], full),
        "calendar.homeassistant": FakeSourceCalendar([], full),
    }

    hass = HomeAssistant()
    hass.data[calendar_module.CALENDAR_DOMAIN] = FakeCalendarComponent(entities)
    merged = calendar_module.MergedCalendarEntity(
        hass,
        "entry-9",
        "Merged",
        list(entities.keys()),
        "calendar.google_work",
    )
    merged._source_map = {"uid\x00event-456": list(owners)}

    _run(
        merged.async_delete_event(
            "event-456",
            recurrence_id="2024-06-01",
            recurrence_range="THISANDFUTURE",
        )
    )

    for entity_id, source in entities.items():
        if entity_id in owners:
            assert source.deleted == [
                {
                    "uid": "event-456",
                    "recurrence_id": "2024-06-01",
                    "recurrence_range": "THISANDFUTURE",
                }
            ]
        else:
            assert source.deleted == []


def test_update_raises_if_no_source_mapping_for_uid(calendar_module):
    full = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
    )
    entities = {"calendar.google_work": FakeSourceCalendar([], full)}
    hass = HomeAssistant()
    hass.data[calendar_module.CALENDAR_DOMAIN] = FakeCalendarComponent(entities)
    merged = calendar_module.MergedCalendarEntity(
        hass,
        "entry-10",
        "Merged",
        ["calendar.google_work"],
        None,
    )

    with pytest.raises(HomeAssistantError, match="could not find source"):
        _run(merged.async_update_event("missing", {"summary": "x"}))


def test_delete_raises_if_no_source_mapping_for_uid(calendar_module):
    full = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
    )
    entities = {"calendar.google_work": FakeSourceCalendar([], full)}
    hass = HomeAssistant()
    hass.data[calendar_module.CALENDAR_DOMAIN] = FakeCalendarComponent(entities)
    merged = calendar_module.MergedCalendarEntity(
        hass,
        "entry-11",
        "Merged",
        ["calendar.google_work"],
        None,
    )

    with pytest.raises(HomeAssistantError, match="could not find source"):
        _run(merged.async_delete_event("missing"))


def test_update_reports_capability_and_runtime_errors(calendar_module):
    full = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
    )
    entities = {
        "calendar.google_work": FakeSourceCalendar([], full),
        "calendar.apple_family": FakeSourceCalendar(
            [], CalendarEntityFeature.CREATE_EVENT
        ),
        "calendar.homeassistant": FakeSourceCalendar(
            [], full, should_raise_on_update=True
        ),
    }
    hass = HomeAssistant()
    hass.data[calendar_module.CALENDAR_DOMAIN] = FakeCalendarComponent(entities)
    merged = calendar_module.MergedCalendarEntity(
        hass,
        "entry-12",
        "Merged",
        list(entities.keys()),
        "calendar.google_work",
    )
    merged._source_map = {
        "uid\x00event-777": [
            "calendar.google_work",
            "calendar.apple_family",
            "calendar.homeassistant",
        ]
    }

    with pytest.raises(HomeAssistantError) as exc:
        _run(merged.async_update_event("event-777", {"summary": "x"}))

    message = str(exc.value)
    assert "does not support updating events" in message
    assert "boom-update" in message


def test_delete_reports_capability_and_runtime_errors(calendar_module):
    full = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
    )
    entities = {
        "calendar.google_work": FakeSourceCalendar([], full),
        "calendar.apple_family": FakeSourceCalendar(
            [], CalendarEntityFeature.UPDATE_EVENT
        ),
        "calendar.homeassistant": FakeSourceCalendar(
            [], full, should_raise_on_delete=True
        ),
    }
    hass = HomeAssistant()
    hass.data[calendar_module.CALENDAR_DOMAIN] = FakeCalendarComponent(entities)
    merged = calendar_module.MergedCalendarEntity(
        hass,
        "entry-13",
        "Merged",
        list(entities.keys()),
        "calendar.google_work",
    )
    merged._source_map = {
        "uid\x00event-888": [
            "calendar.google_work",
            "calendar.apple_family",
            "calendar.homeassistant",
        ]
    }

    with pytest.raises(HomeAssistantError) as exc:
        _run(merged.async_delete_event("event-888"))

    message = str(exc.value)
    assert "does not support deleting events" in message
    assert "boom-delete" in message
