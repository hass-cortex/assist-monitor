"""Tests for ConversationCounter: dedup, persistence, and legacy-key migration."""

from __future__ import annotations

from custom_components.assist_monitor.const import SCOPE_LATEST
from custom_components.assist_monitor.counter import ConversationCounter
from tests.conftest import _Store


async def test_counts_once_per_run_id(mock_hass) -> None:
    counter = ConversationCounter(mock_hass)
    assert counter.observe({SCOPE_LATEST: "run1", "pipe1": "run1"}) == {
        SCOPE_LATEST: 1,
        "pipe1": 1,
    }
    # Same run observed again -> no bump.
    assert counter.observe({SCOPE_LATEST: "run1", "pipe1": "run1"}) == {
        SCOPE_LATEST: 1,
        "pipe1": 1,
    }
    # New run -> bump; a scope with no current run keeps its count in the snapshot.
    assert counter.observe({SCOPE_LATEST: "run2"}) == {SCOPE_LATEST: 2, "pipe1": 1}


async def test_none_run_id_never_counts(mock_hass) -> None:
    counter = ConversationCounter(mock_hass)
    assert counter.observe({SCOPE_LATEST: None}) == {}


async def test_counts_and_seen_persist_across_instances(mock_hass) -> None:
    counter = ConversationCounter(mock_hass)
    counter.observe({SCOPE_LATEST: "run1"})

    restarted = ConversationCounter(mock_hass)
    await restarted.restore()
    # The same run re-observed after a reload must NOT re-count (seen is persisted too).
    assert restarted.observe({SCOPE_LATEST: "run1"}) == {SCOPE_LATEST: 1}


async def test_restore_migrates_legacy_global_scope(mock_hass) -> None:
    """Pre-refactor deployments persisted the global scope as "__global__"."""
    _Store.storage["assist_monitor_counts"] = {
        "counts": {"__global__": 7, "pipe1": 3},
        "seen": {"__global__": "run9"},
    }
    counter = ConversationCounter(mock_hass)
    await counter.restore()
    assert counter.observe({}) == {SCOPE_LATEST: 7, "pipe1": 3}
    # The migrated seen map still dedups the current run.
    assert counter.observe({SCOPE_LATEST: "run9"}) == {SCOPE_LATEST: 7, "pipe1": 3}
