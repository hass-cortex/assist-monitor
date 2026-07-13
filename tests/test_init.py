"""Tests for setup/unload and the coordinator's per-scope view building."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.helpers import event as event_helper
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.assist_monitor import (
    PLATFORMS,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.assist_monitor.const import SCOPE_LATEST
from custom_components.assist_monitor.coordinator import AssistMonitorCoordinator
from tests.conftest import install_debug_store, make_event, make_run_events


async def test_setup_and_unload_entry(mock_hass, mock_config_entry) -> None:
    """Setup wires the coordinator into runtime_data; the entry owns listener cleanup."""
    install_debug_store(mock_hass, {})

    assert await async_setup_entry(mock_hass, mock_config_entry) is True
    coordinator = mock_config_entry.runtime_data
    assert isinstance(coordinator, AssistMonitorCoordinator)
    # Empty store -> a single empty global view (idle, nothing counted yet).
    assert coordinator.data == {
        SCOPE_LATEST: {"conversation_count": None, "round_key": None}
    }
    mock_hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        mock_config_entry, PLATFORMS
    )
    # Domain-filtered tracker registered, its removal owned by the entry.
    event_helper.async_track_state_change_filtered.assert_called_once()
    track_states = event_helper.async_track_state_change_filtered.call_args.args[1]
    assert track_states.domains == {"assist_satellite"}
    assert track_states.all_states is False
    tracker = event_helper.async_track_state_change_filtered.return_value
    assert tracker.async_remove in mock_config_entry._on_unload

    assert await async_unload_entry(mock_hass, mock_config_entry) is True
    # HA runs entry.async_on_unload callbacks after a successful unload.
    for unsub in mock_config_entry._on_unload:
        unsub()
    tracker.async_remove.assert_called_once()


async def test_view_merges_run_count_and_round_key(
    mock_hass, mock_config_entry
) -> None:
    """A completed run surfaces as the global view AND its pipeline's view."""
    install_debug_store(
        mock_hass,
        {"pipe1": {"run1": ("2026-07-12T10:00:00", make_run_events())}},
    )
    coordinator = AssistMonitorCoordinator(mock_hass, mock_config_entry)
    await coordinator.async_refresh()

    latest = coordinator.data[SCOPE_LATEST]
    assert latest["question"] == "turn on the light"
    assert latest["answer"] == "done"
    assert latest["status"] == "success"
    assert latest["run_id"] == "run1"
    assert latest["conversation_count"] == 1
    assert latest["round_key"].startswith("a:1:")
    assert coordinator.data["pipe1"]["run_id"] == "run1"
    assert coordinator.data["pipe1"]["round_key"] == latest["round_key"]


async def test_counts_bump_once_per_run(mock_hass, mock_config_entry) -> None:
    """The same run refreshed twice counts once; a new run increments."""
    install_debug_store(
        mock_hass,
        {"pipe1": {"run1": ("2026-07-12T10:00:00", make_run_events())}},
    )
    coordinator = AssistMonitorCoordinator(mock_hass, mock_config_entry)
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    assert coordinator.data[SCOPE_LATEST]["conversation_count"] == 1
    assert coordinator.data["pipe1"]["conversation_count"] == 1

    install_debug_store(
        mock_hass,
        {
            "pipe1": {
                "run1": ("2026-07-12T10:00:00", make_run_events()),
                "run2": ("2026-07-12T11:00:00", make_run_events(question="hello")),
            }
        },
    )
    await coordinator.async_refresh()
    assert coordinator.data[SCOPE_LATEST]["conversation_count"] == 2
    assert coordinator.data["pipe1"]["conversation_count"] == 2


async def test_counts_survive_restart(mock_hass, mock_config_entry) -> None:
    """Counts persist through the Store and are restored on the next setup."""
    install_debug_store(
        mock_hass,
        {"pipe1": {"run1": ("2026-07-12T10:00:00", make_run_events())}},
    )
    coordinator = AssistMonitorCoordinator(mock_hass, mock_config_entry)
    await coordinator.async_refresh()
    assert coordinator.data[SCOPE_LATEST]["conversation_count"] == 1

    # New coordinator (simulated restart) restores the persisted counts and does
    # not re-count the still-present run.
    restarted = AssistMonitorCoordinator(mock_hass, mock_config_entry)
    await restarted.counter.restore()
    await restarted.async_refresh()
    assert restarted.data[SCOPE_LATEST]["conversation_count"] == 1
    assert restarted.data["pipe1"]["conversation_count"] == 1


async def test_idle_scope_keeps_count_only_view(mock_hass, mock_config_entry) -> None:
    """A scope with persisted counts but no current run still exposes its count."""
    install_debug_store(
        mock_hass,
        {"pipe1": {"run1": ("2026-07-12T10:00:00", make_run_events())}},
    )
    coordinator = AssistMonitorCoordinator(mock_hass, mock_config_entry)
    await coordinator.async_refresh()

    # Debug store cleared (e.g. HA restart with persisted counts).
    install_debug_store(mock_hass, {})
    await coordinator.async_refresh()
    view = coordinator.data["pipe1"]
    assert view["conversation_count"] == 1
    assert "run_id" not in view  # count-only: no conversation fields


async def test_satellite_state_change_triggers_refresh(
    mock_hass, mock_config_entry
) -> None:
    """The tracker action schedules a refresh (domain filtering is done by HA's index)."""
    from homeassistant.core import Event

    coordinator = AssistMonitorCoordinator(mock_hass, mock_config_entry)
    coordinator.attach_listeners()

    action = event_helper.async_track_state_change_filtered.call_args.args[2]
    action(Event({"entity_id": "assist_satellite.kitchen"}))
    mock_hass.async_create_task.assert_called_once()


async def test_broken_debug_store_raises_update_failed(
    mock_hass, mock_config_entry
) -> None:
    """An unexpected debug-store shape surfaces as UpdateFailed, not a raw traceback."""
    mock_hass.data["assist_pipeline"] = SimpleNamespace(
        pipeline_debug=SimpleNamespace()  # present but not a mapping
    )
    coordinator = AssistMonitorCoordinator(mock_hass, mock_config_entry)
    with pytest.raises(UpdateFailed):
        await coordinator.async_refresh()


async def test_missing_debug_store_yields_empty_view(
    mock_hass, mock_config_entry
) -> None:
    """Absent assist_pipeline store degrades to an empty global view, not an error."""
    coordinator = AssistMonitorCoordinator(mock_hass, mock_config_entry)
    await coordinator.async_refresh()
    assert coordinator.data == {
        SCOPE_LATEST: {"conversation_count": None, "round_key": None}
    }


async def test_wake_only_run_is_skipped(mock_hass, mock_config_entry) -> None:
    """A run with no stt-end/error yet (wake-only) is not surfaced."""
    events = [
        make_event("run-start", {"pipeline": "pipe1"}, "2026-07-12T10:00:00+00:00"),
        make_event("wake_word-start", {}, "2026-07-12T10:00:00.5+00:00"),
    ]
    install_debug_store(mock_hass, {"pipe1": {"run1": ("2026-07-12T10:00:00", events)}})
    coordinator = AssistMonitorCoordinator(mock_hass, mock_config_entry)
    await coordinator.async_refresh()
    assert "run_id" not in coordinator.data[SCOPE_LATEST]
    assert "pipe1" not in coordinator.data
