# Calendar Merge — Home Assistant Custom Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/superdingo101/ha-calendar-merge.svg)](https://github.com/superdingo101/ha-calendar-merge/releases)
[![GitHub stars](https://img.shields.io/github/stars/superdingo101/ha-calendar-merge.svg)](https://github.com/superdingo101/ha-calendar-merge/stargazers)
[![GitHub issues](https://img.shields.io/github/issues/superdingo101/ha-calendar-merge.svg)](https://github.com/superdingo101/ha-calendar-merge/issues)
[![License](https://img.shields.io/github/license/superdingo101/ha-calendar-merge.svg)](LICENSE)

Combine multiple Home Assistant `calendar.*` entities into a single, new virtual calendar entity. Each merged calendar is fully independent — you can create as many as you like (e.g. a *Work* calendar merging `calendar.google_work` + `calendar.outlook`, and a *Family* calendar merging `calendar.family` + `calendar.birthdays`).

---

## Features

- **Merge any number** of existing `calendar.*` entities into one.
- **Deduplication with source tracking**: if the same event appears in more than one source calendar (same UID, or same title + start time), it is collapsed to a single entry and a note listing every source calendar is appended to the event description.
- **Full edit support**: selecting an event and using HA's edit/delete UI works transparently — edits and deletes are proxied back to the correct source calendar(s) automatically.
- **Create new events**: configure a default write calendar and new events created from the merged view are saved there.
- **Multiple merged calendars**: create as many merged calendars as you need, each with its own set of sources.
- **Config Flow UI** — configure and edit via *Settings → Integrations*.
- **YAML support** — define merged calendars in `configuration.yaml` (they are imported as config entries automatically).
- **Options flow** — edit the name, source list, or default calendar at any time without restarting HA.

---

## Installation

### HACS (recommended)

1. In Home Assistant, go to **HACS → Integrations**.
2. Click the **⋮** menu (top-right) and choose **Custom repositories**.
3. Add `https://github.com/superdingo101/ha-calendar-merge` as an **Integration**.
4. Search for **Calendar Merge** and install.
5. Restart Home Assistant.

### Manual

1. Copy the `custom_components/calendar_merge` folder into your HA `config/custom_components/` directory.
2. Restart Home Assistant.

---

## Configuration

### Via UI (recommended)

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Calendar Merge**.
3. Enter a name and select the source calendars.
4. Click **Submit** — a new `calendar.<name>` entity appears immediately.

To edit later: open the integration entry and click **Configure**.

### Via YAML

Add to `configuration.yaml`:

```yaml
calendar_merge:
  - name: "Work"
    entity_id:
      - calendar.google_work
      - calendar.outlook_shared
    default_calendar: calendar.google_work   # optional: where new events are created

  - name: "Family"
    entity_id:
      - calendar.google_family
      - calendar.birthdays
      - calendar.school_holidays
```

Each entry creates an independent merged calendar entity.  
**Note:** YAML entries are imported as config entries on startup. Editing them in YAML and restarting HA will update the entry. Deleting a YAML entry will *not* automatically remove the config entry — remove it via the UI.

---

## Editing Events

Selecting any event in the HA calendar card and using the **Edit** or **Delete** buttons works transparently through the merged calendar:

| Action | Behaviour |
|---|---|
| **Edit** a non-duplicate event | Change is proxied to the single source calendar that owns it |
| **Edit** a duplicate event | Change is proxied to **all** source calendars that contain it, and the annotation block is stripped before saving |
| **Delete** a non-duplicate event | Deleted from its source calendar |
| **Delete** a duplicate event | Deleted from **all** source calendars that contain it |
| **Create** a new event | Saved to the configured *Default Calendar for New Events* |

If a source calendar is read-only (e.g. a subscribed CalDAV feed), HA will surface an error for that calendar but still attempt to write to the others.

---

## Deduplication Logic

| Condition | Behaviour |
|---|---|
| Events share a **UID** (e.g. both from CalDAV) | Collapsed; all source calendars listed in description |
| No UID, but same **summary + start time** | Collapsed; all source calendars listed in description |
| Truly different events | Both kept as separate entries |

When collapsed, the event description gains a block like:

```
── Calendar Merge ──
This event appears in multiple calendars:
  • calendar.google_work
  • calendar.outlook_shared
```

The `duplicate_events` attribute on the entity exposes a full map of which events were found in multiple calendars.

---

## Entity Attributes

| Attribute | Description |
|---|---|
| `source_calendars` | List of all source `calendar.*` entity IDs |
| `default_calendar` | The configured default calendar for new events (or `null`) |
| `duplicate_events` | Dict mapping event keys → list of source calendars (only populated when duplicates exist) |

---

## Requirements

- Home Assistant **2023.4** or newer.

---

## Help

Feel free to open an issue if something is not working as expected. 

[![GitHub Issues](https://img.shields.io/badge/GitHub-Issues-green?logo=github&style=for-the-badge)](https://github.com/superdingo101/ha-calendar-merge/issues)

Got questions or thoughts? Want to share your dashboards? You can go on the Home Assistant forum or on the GitHub Discussions section.

[![GitHub Discussions](https://img.shields.io/badge/GitHub-Discussions-lightgrey?logo=github&style=for-the-badge)](https://github.com/superdingo101/ha-calendar-merge/discussions)

<br>

## Donate

If you appreciate my work, any donation would be a great way to show your support 🍻

[![Buy me a beer](https://img.shields.io/badge/Donate-Buy%20me%20a%20beer-yellow?style=for-the-badge&logo=buy-me-a-coffee)](https://buymeacoffee.com/superdingo101)

<br>

Thank you everyone for your support, you all are my greatest motivation!
