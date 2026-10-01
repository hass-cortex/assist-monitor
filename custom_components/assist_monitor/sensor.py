"""Sensors exposing the latest Assist conversation, per assist (pipeline) and globally.

One **Assist Monitor (Latest)** device shows the most recent conversation across every assist. In
addition, one device is created per **Assist pipeline** (the assistants you see under Settings →
Voice assistants: e.g. "Pi", "Claude Voice", "Google"), each tracking *that* assist's latest
conversation and named after it. Per-assist devices are added and removed as pipelines are created or
deleted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.assist_pipeline import (
    PipelineNotFound,
    async_get_pipeline,
    async_get_pipelines,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SCOPE_LATEST
from .coordinator import AssistMonitorConfigEntry, AssistMonitorCoordinator

# Coordinator-driven push entities; no per-entity polling to parallelize.
PARALLEL_UPDATES = 0

_MAX_STATE = 255  # Home Assistant caps entity state length at 255 chars; full text goes in an attribute.


def _truncate(value: Any) -> Any:
    return (
        value[:_MAX_STATE]
        if isinstance(value, str) and len(value) > _MAX_STATE
        else value
    )


@dataclass(frozen=True, kw_only=True)
class AssistMonitorSensorDescription(SensorEntityDescription):
    """Describes an Assist Monitor sensor with value + attribute extractors over the parsed run."""

    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _duration(key: str, name: str) -> AssistMonitorSensorDescription:
    return AssistMonitorSensorDescription(
        key=key,
        name=name,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d, k=key: d.get(k),
    )


def _diag(
    key: str, name: str, icon: str, value_key: str | None = None
) -> AssistMonitorSensorDescription:
    """A simple diagnostic text sensor that reads one field of the run."""
    field = value_key or key
    return AssistMonitorSensorDescription(
        key=key,
        name=name,
        icon=icon,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d, f=field: d.get(f),
    )


SENSORS: tuple[AssistMonitorSensorDescription, ...] = (
    AssistMonitorSensorDescription(
        key="last_question",
        name="Last question",
        icon="mdi:comment-question-outline",
        value_fn=lambda d: _truncate(d.get("question")),
        attrs_fn=lambda d: {
            "conversation_id": d.get("conversation_id"),
            "satellite_id": d.get("satellite_id"),
        },
    ),
    AssistMonitorSensorDescription(
        key="last_answer",
        name="Last answer",
        icon="mdi:comment-text-outline",
        value_fn=lambda d: _truncate(d.get("answer")),
        attrs_fn=lambda d: {
            "full_text": d.get("answer"),
            "response_type": d.get("response_type"),
        },
    ),
    AssistMonitorSensorDescription(
        key="mode",
        name="Mode",
        icon="mdi:brain",
        value_fn=lambda d: d.get("mode"),
        attrs_fn=lambda d: {
            "used_llm": d.get("used_llm"),
            "processed_locally": d.get("processed_locally"),
            "engine": d.get("engine"),
        },
    ),
    AssistMonitorSensorDescription(
        key="status",
        name="Status",
        icon="mdi:check-circle-outline",
        value_fn=lambda d: d.get("status"),
        attrs_fn=lambda d: {"error_message": d.get("error_message")},
    ),
    AssistMonitorSensorDescription(
        key="assist",
        name="Assist",
        icon="mdi:robot-outline",
        value_fn=lambda d: d.get("pipeline_name") or d.get("pipeline_id"),
        attrs_fn=lambda d: {
            "pipeline_id": d.get("pipeline_id"),
            "engine": d.get("engine"),
        },
    ),
    AssistMonitorSensorDescription(
        key="satellite",
        name="Satellite",
        icon="mdi:account-voice",
        entity_category=EntityCategory.DIAGNOSTIC,
        # Raw entity id, machine-facing; the human-facing versions are the Device/Area
        # sensors, and satellite_id also rides as a last_question attribute.
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("satellite_id"),
        attrs_fn=lambda d: {"engine": d.get("engine")},
    ),
    AssistMonitorSensorDescription(
        key="tool_calls",
        name="Tool calls",
        icon="mdi:tools",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("tool_calls"),
        attrs_fn=lambda d: {
            "tools": d.get("tool_names"),
            "errors": d.get("tool_error_count"),
            "failed_tools": d.get("failed_tools"),
        },
    ),
    AssistMonitorSensorDescription(
        key="tool_errors",
        name="Tool errors",
        icon="mdi:tools",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("tool_error_count"),
        attrs_fn=lambda d: {"failed_tools": d.get("failed_tools")},
    ),
    AssistMonitorSensorDescription(
        key="conversations",
        name="Conversations",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.get("conversation_count"),
    ),
    AssistMonitorSensorDescription(
        key="wake_word",
        name="Wake word",
        icon="mdi:microphone-message",
        value_fn=lambda d: d.get("wake_word"),
    ),
    AssistMonitorSensorDescription(
        key="continue_conversation",
        name="Continue conversation",
        icon="mdi:chat-question-outline",
        value_fn=lambda d: d.get("continue_conversation"),
    ),
    AssistMonitorSensorDescription(
        key="area",
        name="Area",
        icon="mdi:map-marker-outline",
        value_fn=lambda d: d.get("area"),
        attrs_fn=lambda d: {"device": d.get("device_name")},
    ),
    _diag("device", "Device", "mdi:tablet-dashboard", "device_name"),
    _diag("language", "Language", "mdi:translate"),
    _diag("stt_engine", "STT engine", "mdi:microphone"),
    _diag("tts_engine", "TTS engine", "mdi:account-voice"),
    _diag("tts_voice", "TTS voice", "mdi:account-music-outline"),
    _diag("error_code", "Error code", "mdi:alert-circle-outline"),
    AssistMonitorSensorDescription(
        key="question_time",
        name="Question time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d.get("question_time"),
    ),
    _duration("wake_word_seconds", "Wake word seconds"),
    _duration("stt_seconds", "STT seconds"),
    _duration("speech_seconds", "Speech seconds"),
    _duration("intent_seconds", "Intent seconds"),
    _duration("ttft_seconds", "Time to first token"),
    _duration("tts_seconds", "TTS seconds"),
    _duration("latency_seconds", "Speech-to-reply latency"),
    _duration("total_seconds", "Total seconds"),
)

# A consumer-ordering sentinel, kept OUT of SENSORS so it can be appended LAST in code (see
# _add_scope) rather than relying on tuple position -- adding a sensor to SENSORS can never
# accidentally emit after it. It is written AFTER every other field, so on an ordered HA->device state
# stream (e.g. ESPHome's per-entity push) it arrives last: a consumer subscribing to just this sensor
# is guaranteed every other field of the round is already present when it changes, giving event-ordered
# aggregation with no cross-entity read race and no timing settle. The value is
# "<phase>:<n>[:<answer_hash>]" (phase q/a/e, n = conversation number), so the consumer can branch on
# the phase and detect a new round by n. The value is the view's round_key field, computed by the
# coordinator (see coordinator._async_update_data).
ROUND_KEY_SENSOR = AssistMonitorSensorDescription(
    key="round_key",
    name="Round key",
    icon="mdi:key-variant",
    entity_category=EntityCategory.DIAGNOSTIC,
    # Niche machine-consumer sentinel (e-paper/ESPHome); opt-in via entity registry.
    entity_registry_enabled_default=False,
    value_fn=lambda d: d.get("round_key"),
)


def _pipeline_label(hass: HomeAssistant, pipeline_id: str) -> str:
    """The assist (pipeline) name, e.g. "Pi" / "Claude Voice"; falls back to the id if unknown."""
    try:
        return async_get_pipeline(hass, pipeline_id).name
    except KeyError, PipelineNotFound:  # removed pipeline / store not loaded
        return pipeline_id


def _current_pipelines(
    hass: HomeAssistant, coordinator: AssistMonitorCoordinator
) -> set[str]:
    """All configured pipeline ids, plus any seen in recent runs (defensive).

    Count-only views (idle scopes with no current run) do NOT create devices — a deleted
    pipeline's persisted count must not resurrect its device.
    """
    pipelines: set[str] = set()
    try:
        pipelines = {pipeline.id for pipeline in async_get_pipelines(hass)}
    except KeyError:  # assist_pipeline store not loaded
        pipelines = set()
    data = coordinator.data or {}
    pipelines.update(
        scope
        for scope, view in data.items()
        if scope != SCOPE_LATEST and "run_id" in view
    )
    return pipelines


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AssistMonitorConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Latest device plus one device per assist pipeline, kept in sync as pipelines change."""
    _DeviceManager(hass, entry, async_add_entities).async_setup()


class _DeviceManager:
    """Creates/removes per-assist (per-pipeline) devices as Assist pipelines come and go."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: AssistMonitorConfigEntry,
        async_add_entities: AddEntitiesCallback,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator: AssistMonitorCoordinator = entry.runtime_data
        self._async_add_entities = async_add_entities
        self._scopes: set[str] = set()

    @callback
    def async_setup(self) -> None:
        self._add_scope(SCOPE_LATEST)  # global "most recent" device
        self._sync()  # one device per current pipeline
        self._cleanup_stale_devices()  # drop devices from earlier layouts (one-time)
        # Pipelines are not entity-registry items, so re-sync on each coordinator tick to pick
        # up created/deleted assists.
        self.entry.async_on_unload(self.coordinator.async_add_listener(self._sync))

    @callback
    def _sync(self) -> None:
        current = _current_pipelines(self.hass, self.coordinator)
        for pipeline_id in current - self._scopes:
            self._add_scope(pipeline_id)
        for scope in list(self._scopes):
            if scope != SCOPE_LATEST and scope not in current:
                self._remove_scope(scope)

    @callback
    def _add_scope(self, scope: str) -> None:
        entities = [
            AssistMonitorSensor(self.coordinator, self.entry, desc, scope)
            for desc in SENSORS
        ]
        # Append the ordering sentinel LAST, structurally -- this (not tuple position) is what
        # guarantees round_key registers after every other sensor and so is emitted last on the
        # ordered state stream. Keeping it out of SENSORS makes that guarantee impossible to break by
        # editing SENSORS. See ROUND_KEY_SENSOR.
        entities.append(
            AssistMonitorSensor(self.coordinator, self.entry, ROUND_KEY_SENSOR, scope)
        )
        self._scopes.add(scope)
        self._async_add_entities(entities)

    @callback
    def _remove_scope(self, scope: str) -> None:
        self._scopes.discard(scope)
        self._remove_device(scope)

    @callback
    def _remove_device(self, scope: str) -> None:
        # The registry cascade removes the device's entities, including the live
        # entity objects.
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device_by_identifier(
            (DOMAIN, f"{self.entry.entry_id}_{scope}"), self.entry.entry_id
        )
        if device is not None:
            device_registry.async_remove_device(device.id)

    @callback
    def _cleanup_stale_devices(self) -> None:
        """Remove devices from this entry that no longer correspond to a current scope."""
        valid = {(DOMAIN, f"{self.entry.entry_id}_{scope}") for scope in self._scopes}
        device_registry = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(
            device_registry, self.entry.entry_id
        ):
            if not device.identifiers & valid:
                device_registry.async_remove_device(device.id)


class AssistMonitorSensor(CoordinatorEntity[AssistMonitorCoordinator], SensorEntity):
    """A field of either the global-latest conversation or one assist's latest conversation."""

    _attr_has_entity_name = True
    entity_description: AssistMonitorSensorDescription

    def __init__(
        self,
        coordinator: AssistMonitorCoordinator,
        entry: AssistMonitorConfigEntry,
        description: AssistMonitorSensorDescription,
        scope: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._scope = scope  # SCOPE_LATEST or a pipeline id
        self._attr_unique_id = f"{entry.entry_id}_{scope}_{description.key}"
        if scope == SCOPE_LATEST:
            device_name = "Assist Monitor (Latest)"
        else:
            device_name = f"Assist: {_pipeline_label(coordinator.hass, scope)}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{scope}")},
            name=device_name,
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="hass-cortex",
            model="All assists" if scope == SCOPE_LATEST else "Assist pipeline",
            sw_version=coordinator.version,
            configuration_url="https://github.com/hass-cortex/assist-monitor",
        )

    @property
    def _view(self) -> dict[str, Any]:
        """This scope's view — the ONE structure sensors read (see coordinator.py)."""
        return (self.coordinator.data or {}).get(self._scope) or {}

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._view)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        view = self._view
        # Attributes describe a conversation; a count-only view (idle scope) has none.
        if self.entity_description.attrs_fn is None or "run_id" not in view:
            return None
        return self.entity_description.attrs_fn(view)
