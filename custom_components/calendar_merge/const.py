"""Constants for Calendar Merge integration."""

DOMAIN = "calendar_merge"

CONF_SOURCE_CALENDARS = "entity_id"
CONF_CALENDAR_NAME = "name"
CONF_DEFAULT_CALENDAR = "default_calendar"

PLATFORMS = ["calendar"]

# How far ahead to look when determining the "current/next" event for entity state
LOOKAHEAD_HOURS = 24

# Sentinel prefix written into event descriptions to track source calendars.
# Stored as an HTML comment so most calendar UIs hide it automatically.
SOURCES_SENTINEL = "<!-- cm-sources:"
SOURCES_SENTINEL_END = "-->"

# Human-readable duplicate block sentinel (used for stripping before proxying writes)
MERGE_BLOCK_SENTINEL = "\n\n── Calendar Merge ──\n"
