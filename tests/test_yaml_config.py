from __future__ import annotations

import importlib.util
import asyncio
from pathlib import Path
import sys
import types

import pytest


def _install_ha_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    vol_mod = types.ModuleType("voluptuous")
    vol_mod.ALLOW_EXTRA = object()
    vol_mod.Required = lambda key, default=None: key
    vol_mod.Optional = lambda key, default=None: key
    vol_mod.All = lambda *validators: validators[0] if validators else (lambda v: v)

    class _Schema:
        def __init__(self, *_args, **_kwargs):
            pass

        def __call__(self, value):
            return value

    vol_mod.Schema = _Schema
    monkeypatch.setitem(sys.modules, "voluptuous", vol_mod)

    ha_pkg = types.ModuleType("homeassistant")
    monkeypatch.setitem(sys.modules, "homeassistant", ha_pkg)

    config_entries_mod = types.ModuleType("homeassistant.config_entries")
    config_entries_mod.SOURCE_IMPORT = "import"
    config_entries_mod.ConfigEntry = object
    config_entries_mod.ConfigFlow = type("ConfigFlow", (), {"__init_subclass__": classmethod(lambda cls, **kwargs: None)})
    config_entries_mod.OptionsFlow = object
    monkeypatch.setitem(sys.modules, "homeassistant.config_entries", config_entries_mod)

    core_mod = types.ModuleType("homeassistant.core")
    core_mod.HomeAssistant = object
    core_mod.callback = lambda fn: fn
    monkeypatch.setitem(sys.modules, "homeassistant.core", core_mod)

    helpers_pkg = types.ModuleType("homeassistant.helpers")
    cv_mod = types.ModuleType("homeassistant.helpers.config_validation")
    cv_mod.string = str
    cv_mod.entity_id = str
    cv_mod.ensure_list = lambda value: value if isinstance(value, list) else [value]
    helpers_pkg.config_validation = cv_mod
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", helpers_pkg)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.config_validation", cv_mod)

    selector_mod = types.ModuleType("homeassistant.helpers.selector")
    selector_mod.EntitySelector = lambda *_args, **_kwargs: object()
    selector_mod.EntitySelectorConfig = lambda *_args, **_kwargs: object()
    selector_mod.TextSelector = lambda *_args, **_kwargs: object()
    selector_mod.TextSelectorConfig = lambda *_args, **_kwargs: object()
    selector_mod.TextSelectorType = types.SimpleNamespace(TEXT="text")
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.selector", selector_mod)


@pytest.fixture
def modules(monkeypatch: pytest.MonkeyPatch):
    _install_ha_stubs(monkeypatch)
    root = Path(__file__).resolve().parents[1]

    const_spec = importlib.util.spec_from_file_location(
        "custom_components.calendar_merge.const",
        root / "custom_components" / "calendar_merge" / "const.py",
    )
    const_mod = importlib.util.module_from_spec(const_spec)
    assert const_spec and const_spec.loader
    const_spec.loader.exec_module(const_mod)
    monkeypatch.setitem(sys.modules, "custom_components.calendar_merge.const", const_mod)

    init_spec = importlib.util.spec_from_file_location(
        "custom_components.calendar_merge.__init__",
        root / "custom_components" / "calendar_merge" / "__init__.py",
    )
    init_mod = importlib.util.module_from_spec(init_spec)
    assert init_spec and init_spec.loader
    init_spec.loader.exec_module(init_mod)

    flow_spec = importlib.util.spec_from_file_location(
        "custom_components.calendar_merge.config_flow",
        root / "custom_components" / "calendar_merge" / "config_flow.py",
    )
    flow_mod = importlib.util.module_from_spec(flow_spec)
    assert flow_spec and flow_spec.loader
    flow_spec.loader.exec_module(flow_mod)

    return init_mod, flow_mod, const_mod


def test_readme_yaml_example_validates(modules):
    init_mod, _flow_mod, _const = modules
    cfg = {
        "calendar_merge": [
            {
                "name": "Work",
                "entity_id": ["calendar.google_work", "calendar.outlook_shared"],
                "default_calendar": "calendar.google_work",
            },
            {
                "name": "Family",
                "entity_id": [
                    "calendar.google_family",
                    "calendar.birthdays",
                    "calendar.school_holidays",
                ],
            },
        ]
    }
    validated = init_mod.CONFIG_SCHEMA(cfg)
    assert validated["calendar_merge"][0]["default_calendar"] == "calendar.google_work"


def test_yaml_import_updates_existing_entry(modules):
    _init_mod, flow_mod, const = modules

    class FakeEntry:
        entry_id = "abc"
        data = {const.CONF_CALENDAR_NAME: "Work"}

    updated = {}
    reloaded = []

    class FakeConfigEntries:
        def async_update_entry(self, entry, data):
            updated["entry"] = entry
            updated["data"] = data

        async def async_reload(self, entry_id):
            reloaded.append(entry_id)

    flow = flow_mod.CalendarMergeConfigFlow()
    flow.hass = types.SimpleNamespace(config_entries=FakeConfigEntries())
    flow._async_current_entries = lambda: [FakeEntry()]
    flow.async_abort = lambda **kwargs: kwargs

    result = asyncio.run(
        flow.async_step_import(
            {
                const.CONF_CALENDAR_NAME: "Work",
                const.CONF_SOURCE_CALENDARS: ["calendar.google_work"],
                const.CONF_DEFAULT_CALENDAR: "calendar.google_work",
            }
        )
    )

    assert result == {"reason": "already_configured"}
    assert updated["entry"].entry_id == "abc"
    assert updated["data"][const.CONF_DEFAULT_CALENDAR] == "calendar.google_work"
    assert reloaded == ["abc"]
