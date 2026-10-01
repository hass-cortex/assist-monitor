"""Tests for the sensor platform: value extraction, scoping, and device management."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.components import assist_pipeline
from homeassistant.helpers import device_registry as dr

from custom_components.assist_monitor.const import DOMAIN, SCOPE_LATEST
from custom_components.assist_monitor.coordinator import AssistMonitorCoordinator
from custom_components.assist_monitor.sensor import (
    ROUND_KEY_SENSOR,
    SENSORS,
    AssistMonitorSensor,
    _DeviceManager,
    _truncate,
    async_setup_entry,
)
from tests.conftest import install_debug_store, make_run_events


def _sensor(
    coordinator, entry, key: str, scope: str = SCOPE_LATEST
) -> AssistMonitorSensor:
    description = next(d for d in SENSORS if d.key == key)
    return AssistMonitorSensor(coordinator, entry, description, scope)


def _coordinator_with(mock_hass, mock_config_entry, data) -> AssistMonitorCoordinator:
    coordinator = AssistMonitorCoordinator(mock_hass, mock_config_entry)
    coordinator.data = data
    return coordinator


SAMPLE_VIEW = {
    "run_id": "run1",
    "question": "turn on the light",
    "answer": "done",
    "mode": "llm",
    "used_llm": True,
    "processed_locally": False,
    "status": "success",
    "pipeline_id": "pipe1",
    "pipeline_name": "Pi",
    "satellite_id": "assist_satellite.kitchen",
    "conversation_id": "C1",
    "response_type": "action_done",
    "engine": "conversation.pi",
    "tool_calls": 2,
    "tool_names": ["ha_get_state", "ha_call_service"],
    "tool_error_count": 1,
    "failed_tools": ["ha_call_service"],
    "intent_seconds": 1.9,
    "latency_seconds": 2.5,
    "conversation_count": 7,
    "round_key": "a:7:abcd1234",
}


def test_truncate_caps_at_255() -> None:
    assert _truncate("x" * 300) == "x" * 255
    assert _truncate("short") == "short"
    assert _truncate(None) is None


def test_sensor_values_from_view(mock_hass, mock_config_entry) -> None:
    """Each sensor reads its field from the scope's view — uniformly via value_fn."""
    coordinator = _coordinator_with(
        mock_hass, mock_config_entry, {SCOPE_LATEST: SAMPLE_VIEW}
    )
    values = {
        "last_question": "turn on the light",
        "last_answer": "done",
        "mode": "llm",
        "status": "success",
        "assist": "Pi",
        "satellite": "assist_satellite.kitchen",
        "tool_calls": 2,
        "tool_errors": 1,
        "intent_seconds": 1.9,
        "latency_seconds": 2.5,
        "conversations": 7,
    }
    for key, expected in values.items():
        assert _sensor(coordinator, mock_config_entry, key).native_value == expected

    round_key = AssistMonitorSensor(
        coordinator, mock_config_entry, ROUND_KEY_SENSOR, SCOPE_LATEST
    )
    assert round_key.native_value == "a:7:abcd1234"


def test_long_answer_truncated_with_full_text_attr(
    mock_hass, mock_config_entry
) -> None:
    """State is capped at 255 chars; the full reply lives in the attribute."""
    view = {**SAMPLE_VIEW, "answer": "y" * 300}
    coordinator = _coordinator_with(mock_hass, mock_config_entry, {SCOPE_LATEST: view})
    sensor = _sensor(coordinator, mock_config_entry, "last_answer")
    assert sensor.native_value == "y" * 255
    assert sensor.extra_state_attributes["full_text"] == "y" * 300


def test_assist_falls_back_to_pipeline_id(mock_hass, mock_config_entry) -> None:
    view = {**SAMPLE_VIEW, "pipeline_name": None}
    coordinator = _coordinator_with(mock_hass, mock_config_entry, {SCOPE_LATEST: view})
    assert _sensor(coordinator, mock_config_entry, "assist").native_value == "pipe1"


def test_empty_view_yields_none(mock_hass, mock_config_entry) -> None:
    """An idle scope (count-only or empty view) reports None state and no attributes."""
    coordinator = _coordinator_with(
        mock_hass,
        mock_config_entry,
        {SCOPE_LATEST: {"conversation_count": None, "round_key": None}},
    )
    sensor = _sensor(coordinator, mock_config_entry, "last_question")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes is None


def test_count_only_view_shows_count_without_attrs(
    mock_hass, mock_config_entry
) -> None:
    """Idle scope with persisted count: Conversations shows it; run sensors stay None."""
    coordinator = _coordinator_with(
        mock_hass,
        mock_config_entry,
        {SCOPE_LATEST: {"conversation_count": 7, "round_key": None}},
    )
    assert _sensor(coordinator, mock_config_entry, "conversations").native_value == 7
    answer = _sensor(coordinator, mock_config_entry, "last_answer")
    assert answer.native_value is None
    assert answer.extra_state_attributes is None  # no run_id -> no attributes


def test_pipeline_scope_reads_its_own_view(mock_hass, mock_config_entry) -> None:
    """A per-assist sensor reads its own pipeline's view, not the global latest."""
    other = {**SAMPLE_VIEW, "question": "other question", "conversation_count": 3}
    coordinator = _coordinator_with(
        mock_hass,
        mock_config_entry,
        {SCOPE_LATEST: SAMPLE_VIEW, "pipe2": other},
    )
    sensor = _sensor(coordinator, mock_config_entry, "last_question", scope="pipe2")
    assert sensor.native_value == "other question"
    scoped = _sensor(coordinator, mock_config_entry, "conversations", scope="pipe2")
    assert scoped.native_value == 3


def test_unique_id_and_device_names(mock_hass, mock_config_entry) -> None:
    """Unique id is entry+scope+key; device name reflects the scope."""
    assist_pipeline.async_get_pipeline.side_effect = None
    assist_pipeline.async_get_pipeline.return_value = SimpleNamespace(name="Pi")
    coordinator = _coordinator_with(mock_hass, mock_config_entry, {SCOPE_LATEST: {}})
    latest = _sensor(coordinator, mock_config_entry, "mode")
    assert latest._attr_unique_id == "test_entry_123_latest_mode"
    assert latest._attr_device_info["name"] == "Assist Monitor (Latest)"

    scoped = _sensor(coordinator, mock_config_entry, "mode", scope="pipe1")
    assert scoped._attr_device_info["name"] == "Assist: Pi"


async def test_device_manager_creates_and_removes_scopes(
    mock_hass, mock_config_entry
) -> None:
    """One device for Latest plus one per pipeline; round_key registers last."""
    install_debug_store(
        mock_hass,
        {"pipe1": {"run1": ("2026-07-12T10:00:00", make_run_events())}},
    )
    assist_pipeline.async_get_pipelines.return_value = [SimpleNamespace(id="pipe1")]
    coordinator = AssistMonitorCoordinator(mock_hass, mock_config_entry)
    await coordinator.async_refresh()
    mock_config_entry.runtime_data = coordinator

    added: list = []
    await async_setup_entry(
        mock_hass, mock_config_entry, lambda entities: added.extend(entities)
    )

    # Latest + pipe1, each with all SENSORS plus the round_key sentinel appended last.
    assert len(added) == 2 * (len(SENSORS) + 1)
    scopes = {entity._scope for entity in added}
    assert scopes == {SCOPE_LATEST, "pipe1"}
    per_scope = [e for e in added if e._scope == "pipe1"]
    assert per_scope[-1].entity_description.key == "round_key"

    # Pipeline deleted -> its device is removed on the next coordinator tick (the
    # registry cascade removes its entities), even though its count-only view lingers
    # (persisted counts never resurrect a device).
    assist_pipeline.async_get_pipelines.return_value = []
    install_debug_store(mock_hass, {})
    registry = dr.async_get(mock_hass)
    device = SimpleNamespace(id="device_pipe1")
    registry.async_get_device_by_identifier.return_value = device
    await coordinator.async_refresh()
    assert coordinator.data["pipe1"]["conversation_count"] == 1  # count-only view stays
    registry.async_get_device_by_identifier.assert_called_with(
        (DOMAIN, f"{mock_config_entry.entry_id}_pipe1"), mock_config_entry.entry_id
    )
    registry.async_remove_device.assert_called_once_with("device_pipe1")


async def test_device_manager_adds_new_pipeline_on_tick(
    mock_hass, mock_config_entry
) -> None:
    """A pipeline created later gets its device on the next coordinator refresh."""
    install_debug_store(mock_hass, {})
    coordinator = AssistMonitorCoordinator(mock_hass, mock_config_entry)
    await coordinator.async_refresh()
    mock_config_entry.runtime_data = coordinator

    added: list = []
    manager = _DeviceManager(
        mock_hass, mock_config_entry, lambda entities: added.extend(entities)
    )
    manager.async_setup()
    assert {e._scope for e in added} == {SCOPE_LATEST}

    assist_pipeline.async_get_pipelines.return_value = [SimpleNamespace(id="pipe_new")]
    await coordinator.async_refresh()
    assert {e._scope for e in added} == {SCOPE_LATEST, "pipe_new"}
