# assist-monitor

HA custom integration exposing the latest Assist conversation (per pipeline + across all) as
real-time sensors, read directly from the `assist_pipeline` in-memory debug store.

## Tech Stack

- **Runtime**: Python 3.14+, no runtime dependencies
- **Package manager**: `uv` (not pip)
- **Linting**: ruff (lint + format), pyright (standard mode)

## Build & Test

```bash
uv sync --group dev --group test
uv run pytest tests/ -v
uv run ruff check . && uv run ruff format .
uv run pyright
```

## Architecture

```
custom_components/assist_monitor/
├── __init__.py          # Thin entry points (setup/unload) only
├── coordinator.py       # THE scope authority: 5s safety-net poll + debounced (0.3s)
│                        #   assist_satellite instant refresh (domain-filtered tracker);
│                        #   publishes data = {scope: view} (view = run + conversation_count +
│                        #   round_key); annotation (pipeline/device/area names)
├── counter.py           # ConversationCounter: per-scope run_id dedup + Store persistence
│                        #   (+ "__global__" -> "latest" legacy-key migration)
├── pipeline_reader.py   # PURE event flattening (no HA imports; unit-testable standalone) +
│                        #   round_key phase logic (q/a/e/x) + debug-store scanning
├── sensor.py            # One "Latest" device + one device per Assist pipeline (dynamic add/remove);
│                        #   ~26 sensors per device; ROUND_KEY_SENSOR appended last (ordering sentinel)
├── config_flow.py       # Single-instance, zero-config flow
├── const.py             # DOMAIN, POLL_SECONDS, SCOPE_LATEST
├── strings.json         # UI strings (source of truth)
└── translations/en.json # Must match strings.json
```

## Key Design Points

- **Data source**: `hass.data["assist_pipeline"].pipeline_debug` (internal, last 10 runs per
  pipeline). Read-only; resilient to absence; NOT a stable public API.
- **Per-scope views (ScopeView)**: `coordinator.data` maps scope (`SCOPE_LATEST` or pipeline id) to
  ONE view dict; sensors read fields uniformly via `value_fn(view)` — no scope logic in sensor.py.
  A count-only view (idle/deleted pipeline) has no `run_id`: that key gates attributes AND device
  creation (a deleted pipeline's persisted count must not resurrect its device).
- **round_key ordering sentinel**: kept OUT of `SENSORS` and appended last in `_add_scope` so it
  always registers (and emits) after every other sensor — consumers (e-paper/ESPHome) subscribe to
  it alone for race-free round aggregation. Never add it to `SENSORS`.
- **Counts persistence** (`counter.py`): cumulative per-scope conversation counts AND the last-seen
  run_id map are persisted together so reloads don't re-count the current conversation.
- **Tests**: `tests/conftest.py` mocks the full homeassistant hierarchy; production modules capture
  `async_get_pipeline(s)` bindings at import time, so tests configure the shared MagicMock objects
  in place (never reassign module attributes).

## Conventions

- **Commits**: Conventional Commits, enforced by commitizen commit-msg hook
- **Version**: `pyproject.toml` + `manifest.json` must match the release tag (`uv run cz bump`)
- **Translations**: `strings.json` is source of truth; `translations/en.json` must match
