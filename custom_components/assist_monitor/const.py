"""Constants for the Assist Monitor integration."""

DOMAIN = "assist_monitor"

# How often to scan the pipeline debug store as a universal safety net. Satellite state
# changes trigger a debounced immediate refresh, so this only bounds latency for
# non-satellite runs (HA app / web text chat).
POLL_SECONDS = 5

# Cooldown of the satellite-triggered request_refresh debouncer. Overrides the coordinator
# default (10s), which would coalesce a whole voice round into one late refresh.
REFRESH_DEBOUNCE_SECONDS = 0.3

# Domain whose state changes signal Assist activity (drives the instant-refresh tracker).
SATELLITE_DOMAIN = "assist_satellite"

# Scope key of the global "most recent conversation across all assists" view/device.
# Every other scope is an Assist pipeline id (a ULID, so no collision is possible).
SCOPE_LATEST = "latest"
