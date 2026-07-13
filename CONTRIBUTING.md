# Contributing to Assist Monitor

Thank you for considering contributing to this project. This guide covers the development setup, testing, and submission process.

## Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) package manager
- A Home Assistant instance with at least one Assist pipeline (for manual testing)

## Development Setup

```bash
git clone https://github.com/hass-cortex/assist-monitor.git
cd assist-monitor
uv sync --group dev --group test
uv run pre-commit install --hook-type pre-commit --hook-type pre-push --hook-type commit-msg
```

## Running Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run with coverage report
uv run pytest tests/ --cov=custom_components --cov-report=term-missing

# Run a specific test file
uv run pytest tests/test_sensor.py -v
```

Tests mock the `homeassistant` module hierarchy in `tests/conftest.py`, so no
Home Assistant installation is required. `tests/test_pipeline_reader.py` loads
`pipeline_reader.py` standalone (it is deliberately HA-free).

## Lint & Type Check

```bash
uv run ruff check .          # Lint
uv run ruff format .         # Format
uv run pyright               # Type check
```

## Commit Convention

Use [Conventional Commits](https://www.conventionalcommits.org/):

| Prefix | Use case |
|--------|----------|
| `feat:` | New feature |
| `fix:` | Bug fix |
| `docs:` | Documentation only |
| `chore:` | Maintenance / tooling |
| `refactor:` | Code restructure without behavior change |
| `test:` | Adding or updating tests |

The commit-msg hook (commitizen) enforces this format.

## Release Flow (maintainers)

Versions live in `pyproject.toml` and `custom_components/assist_monitor/manifest.json`
and must match the git tag — CI verifies this on release.

```bash
uv run cz bump          # bumps version files, creates the annotated tag
git push --follow-tags  # tag push triggers the Release workflow
```

The Release workflow validates (ruff + pytest), verifies version consistency,
generates changelog notes, and publishes a GitHub Release with
`assist_monitor.zip` attached.

## Manual Testing Against Home Assistant

Copy `custom_components/assist_monitor/` into your HA `config/custom_components/`
and restart Home Assistant, then add the integration via
**Settings → Devices & services → Add integration → Assist Monitor**.
Trigger a voice conversation and watch the `sensor.assist_monitor_latest_*`
entities update.

## Submitting Changes

1. Fork the repository and create a feature branch.
2. Make your changes with tests.
3. Ensure `uv run ruff check .`, `uv run pyright`, and `uv run pytest` all pass.
4. Open a pull request with a clear description.
