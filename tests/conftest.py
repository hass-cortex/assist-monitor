"""Test fixtures for assist-monitor.

Mocks the homeassistant module hierarchy so that custom_components
can be imported without real dependencies.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── Mock homeassistant module hierarchy ──
_ha = ModuleType("homeassistant")
_ha_core = ModuleType("homeassistant.core")
_ha_config_entries = ModuleType("homeassistant.config_entries")
_ha_const = ModuleType("homeassistant.const")
_ha_data_entry_flow = ModuleType("homeassistant.data_entry_flow")
_ha_components = ModuleType("homeassistant.components")
_ha_components_ap = ModuleType("homeassistant.components.assist_pipeline")
_ha_components_sensor = ModuleType("homeassistant.components.sensor")
_ha_helpers = ModuleType("homeassistant.helpers")
_ha_helpers_ar = ModuleType("homeassistant.helpers.area_registry")
_ha_helpers_debounce = ModuleType("homeassistant.helpers.debounce")
_ha_helpers_dr = ModuleType("homeassistant.helpers.device_registry")
_ha_helpers_er = ModuleType("homeassistant.helpers.entity_registry")
_ha_helpers_ep = ModuleType("homeassistant.helpers.entity_platform")
_ha_helpers_event = ModuleType("homeassistant.helpers.event")
_ha_helpers_storage = ModuleType("homeassistant.helpers.storage")
_ha_helpers_uc = ModuleType("homeassistant.helpers.update_coordinator")
_ha_loader = ModuleType("homeassistant.loader")


async def _async_get_integration(hass, domain):
    return SimpleNamespace(version="0.0.0")


_ha_loader.async_get_integration = _async_get_integration

# ── Core ──
_ha_core.HomeAssistant = MagicMock
_ha_core.callback = lambda f: f


class _Event:
    """Minimal Event with .data, as used by the coordinator's bus listener."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = data or {}


_ha_core.Event = _Event

# ── data_entry_flow ──


class _AbortFlow(Exception):  # noqa: N818 - mirrors HA's AbortFlow name
    """Raised by _abort_if_unique_id_configured when a duplicate exists."""

    def __init__(self, reason: str = "already_configured") -> None:
        super().__init__(reason)
        self.reason = reason


_ha_data_entry_flow.AbortFlow = _AbortFlow

# ── Config entries ──


class _ConfigEntry:
    """Functional ConfigEntry stand-in (subscriptable for ConfigEntry[T])."""

    def __init__(self, entry_id: str = "test_entry", data: dict | None = None) -> None:
        self.entry_id = entry_id
        self.data = data or {}
        self.runtime_data: Any = None
        self._on_unload: list = []

    def __class_getitem__(cls, item):
        return cls

    def async_on_unload(self, func):
        self._on_unload.append(func)
        return func


class _MockConfigFlow:
    """Mock ConfigFlow base class with unique-id duplicate detection."""

    VERSION = 1
    hass = None
    # Tests seed this to simulate an already-configured instance.
    existing_unique_ids: set[str] = set()

    def __init__(self):
        self.context = {}
        self._unique_id = None

    def __init_subclass__(cls, *, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)

    async def async_set_unique_id(self, unique_id):
        self._unique_id = unique_id

    def _abort_if_unique_id_configured(self, updates=None):
        if self._unique_id in type(self).existing_unique_ids:
            raise _AbortFlow("already_configured")

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}


_ha_config_entries.ConfigEntry = _ConfigEntry
_ha_config_entries.ConfigFlow = _MockConfigFlow
_ha_config_entries.ConfigFlowResult = dict

# ── Constants ──


class _EntityCategory(StrEnum):
    CONFIG = "config"
    DIAGNOSTIC = "diagnostic"


_ha_const.EntityCategory = _EntityCategory
_ha_const.EVENT_STATE_CHANGED = "state_changed"
_ha_const.UnitOfTime = MagicMock()
_ha_const.UnitOfTime.SECONDS = "s"

# ── assist_pipeline component (tests configure these per case) ──


class _PipelineNotFound(Exception):  # noqa: N818 - mirrors HA's PipelineNotFound name
    """Mirrors assist_pipeline.PipelineNotFound (unknown/removed pipeline id)."""


_ha_components_ap.PipelineNotFound = _PipelineNotFound
_ha_components_ap.async_get_pipeline = MagicMock()
_ha_components_ap.async_get_pipelines = MagicMock(return_value=[])

# ── Sensor platform ──


class _SensorStateClass(StrEnum):
    MEASUREMENT = "measurement"
    TOTAL = "total"
    TOTAL_INCREASING = "total_increasing"


class _SensorDeviceClass(StrEnum):
    DURATION = "duration"
    TIMESTAMP = "timestamp"


@dataclass(frozen=True, kw_only=True)
class _SensorEntityDescription:
    key: str = ""
    name: str | None = None
    icon: str | None = None
    native_unit_of_measurement: str | None = None
    suggested_display_precision: int | None = None
    state_class: _SensorStateClass | None = None
    entity_category: _EntityCategory | None = None
    device_class: _SensorDeviceClass | None = None
    entity_registry_enabled_default: bool = True


class _SensorEntity:
    """Mock SensorEntity base class."""

    _attr_unique_id: str | None = None
    _attr_device_info: Any = None
    _attr_has_entity_name: bool = False
    entity_description: Any = None
    hass: Any = None

    async def async_remove(self, force_remove: bool = False) -> None:
        pass

    def async_write_ha_state(self) -> None:
        pass


_ha_components_sensor.SensorEntity = _SensorEntity
_ha_components_sensor.SensorEntityDescription = _SensorEntityDescription
_ha_components_sensor.SensorDeviceClass = _SensorDeviceClass
_ha_components_sensor.SensorStateClass = _SensorStateClass

# ── Registries (fresh MagicMocks per test via the autouse fixture below) ──
_ha_helpers_ar.async_get = MagicMock()
_ha_helpers_dr.async_get = MagicMock()
_ha_helpers_er.async_get = MagicMock()
_ha_helpers_dr.DeviceInfo = dict


class _DeviceEntryType(StrEnum):
    SERVICE = "service"


_ha_helpers_dr.DeviceEntryType = _DeviceEntryType

# helpers package must expose the registry submodules as attributes
_ha_helpers.area_registry = _ha_helpers_ar
_ha_helpers.device_registry = _ha_helpers_dr
_ha_helpers.entity_registry = _ha_helpers_er

# ── Debounce (coordinator passes a custom request_refresh_debouncer) ──


class _Debouncer:
    """Debouncer stand-in: records config; the coordinator fake refreshes directly."""

    def __init__(self, hass, logger, *, cooldown, immediate, function=None) -> None:
        self.cooldown = cooldown
        self.immediate = immediate
        self.function = function

    def __class_getitem__(cls, item):
        return cls


_ha_helpers_debounce.Debouncer = _Debouncer

# ── Event helpers (domain-filtered state tracking) ──


@dataclass
class _TrackStates:
    all_states: bool
    entities: set
    domains: set


_ha_helpers_event.TrackStates = _TrackStates
# Returns a tracker mock exposing .async_remove; reset per test by the autouse fixture.
_ha_helpers_event.async_track_state_change_filtered = MagicMock()

# ── Entity platform ──
_ha_helpers_ep.AddEntitiesCallback = MagicMock

# ── Storage (functional in-memory fake; class-level dict simulates disk) ──


class _Store:
    """In-memory Store: persists across instances via a class-level dict."""

    storage: dict[str, Any] = {}

    def __init__(self, hass, version, key) -> None:
        self.key = key

    async def async_load(self):
        return type(self).storage.get(self.key)

    async def async_save(self, data) -> None:
        type(self).storage[self.key] = data

    def async_delay_save(self, data_func, delay=0) -> None:
        type(self).storage[self.key] = data_func()


_ha_helpers_storage.Store = _Store

# ── Update coordinator (functional: refresh runs _async_update_data) ──


class _DataUpdateCoordinator:
    """Functional DataUpdateCoordinator fake."""

    def __init__(self, hass, logger, *, name="", update_interval=None, **kwargs):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.data: Any = None
        self.last_update_success = True
        self._listeners: list = []

    def __class_getitem__(cls, item):
        return cls

    async def _async_update_data(self):
        raise NotImplementedError

    async def async_refresh(self):
        self.data = await self._async_update_data()
        for listener in list(self._listeners):
            listener()

    async def async_config_entry_first_refresh(self):
        await self.async_refresh()

    async def async_request_refresh(self):
        await self.async_refresh()

    def async_add_listener(self, update_callback, context=None):
        self._listeners.append(update_callback)

        def _unsub():
            self._listeners.remove(update_callback)

        return _unsub


class _CoordinatorEntity:
    """Mock CoordinatorEntity base class."""

    def __init__(self, coordinator):
        self.coordinator = coordinator

    def __class_getitem__(cls, item):
        return cls

    async def async_added_to_hass(self) -> None:
        pass

    def async_write_ha_state(self) -> None:
        pass


class _UpdateFailed(Exception):  # noqa: N818 - mirrors HA's UpdateFailed name
    """Mirrors update_coordinator.UpdateFailed."""


_ha_helpers_uc.DataUpdateCoordinator = _DataUpdateCoordinator
_ha_helpers_uc.CoordinatorEntity = _CoordinatorEntity
_ha_helpers_uc.UpdateFailed = _UpdateFailed

# ── Register all mocked modules ──
for mod_name, mod in [
    ("homeassistant", _ha),
    ("homeassistant.core", _ha_core),
    ("homeassistant.config_entries", _ha_config_entries),
    ("homeassistant.const", _ha_const),
    ("homeassistant.data_entry_flow", _ha_data_entry_flow),
    ("homeassistant.components", _ha_components),
    ("homeassistant.components.assist_pipeline", _ha_components_ap),
    ("homeassistant.components.sensor", _ha_components_sensor),
    ("homeassistant.helpers", _ha_helpers),
    ("homeassistant.helpers.area_registry", _ha_helpers_ar),
    ("homeassistant.helpers.debounce", _ha_helpers_debounce),
    ("homeassistant.helpers.device_registry", _ha_helpers_dr),
    ("homeassistant.helpers.entity_registry", _ha_helpers_er),
    ("homeassistant.helpers.entity_platform", _ha_helpers_ep),
    ("homeassistant.helpers.event", _ha_helpers_event),
    ("homeassistant.helpers.storage", _ha_helpers_storage),
    ("homeassistant.helpers.update_coordinator", _ha_helpers_uc),
    ("homeassistant.loader", _ha_loader),
]:
    sys.modules[mod_name] = mod

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_ha_mocks():
    """Fresh registry/pipeline mocks + clean storage/flow state for every test."""
    _Store.storage = {}
    _MockConfigFlow.existing_unique_ids = set()

    # Registries: default to "nothing found" so annotation degrades to None.
    for mod in (_ha_helpers_ar, _ha_helpers_dr, _ha_helpers_er):
        registry = MagicMock()
        registry.async_get.return_value = None
        registry.async_get_area.return_value = None
        registry.async_get_device_by_identifier.return_value = None
        mod.async_get = MagicMock(return_value=registry)
    # Module-level registry helper looked up at call time (dr.async_entries_for_config_entry).
    _ha_helpers_dr.async_entries_for_config_entry = MagicMock(return_value=[])

    # Mutate (don't reassign): production modules captured these bindings at import
    # time via `from ... import async_get_pipeline`, so identity must be preserved.
    _ha_components_ap.async_get_pipeline.reset_mock()
    _ha_components_ap.async_get_pipeline.side_effect = _PipelineNotFound(
        "unknown pipeline"
    )
    _ha_components_ap.async_get_pipelines.reset_mock()
    _ha_components_ap.async_get_pipelines.side_effect = None
    _ha_components_ap.async_get_pipelines.return_value = []
    tracker_factory = _ha_helpers_event.async_track_state_change_filtered
    tracker_factory.reset_mock()
    tracker_factory.return_value = MagicMock()
    yield


@pytest.fixture
def mock_hass():
    """Create a mock HomeAssistant instance."""
    hass = MagicMock()
    hass.data = {}
    hass.bus = MagicMock()
    hass.bus.async_listen = MagicMock(return_value=MagicMock())
    # Close the coroutine instead of scheduling it (no running loop in unit tests).
    hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    return hass


@pytest.fixture
def mock_config_entry():
    """Create a functional config entry."""
    return _ConfigEntry(entry_id="test_entry_123")


def make_event(event_type: str, data: dict | None, timestamp: str) -> SimpleNamespace:
    """One pipeline debug event (mirrors assist_pipeline's PipelineEvent shape)."""
    return SimpleNamespace(type=event_type, data=data, timestamp=timestamp)


def make_run_events(
    *,
    question: str = "turn on the light",
    answer: str | None = "done",
    satellite_id: str | None = "assist_satellite.kitchen",
    pipeline_id: str = "pipe1",
) -> list[SimpleNamespace]:
    """A minimal complete pipeline run."""
    events = [
        make_event(
            "run-start",
            {
                "conversation_id": "C1",
                "satellite_id": satellite_id,
                "pipeline": pipeline_id,
                "language": "en",
            },
            "2026-07-12T10:00:00.000000+00:00",
        ),
        make_event(
            "stt-end",
            {"stt_output": {"text": question}},
            "2026-07-12T10:00:01.000000+00:00",
        ),
        make_event(
            "intent-start",
            {"engine": "conversation.pi", "device_id": "dev1"},
            "2026-07-12T10:00:01.100000+00:00",
        ),
    ]
    if answer is not None:
        events.append(
            make_event(
                "intent-end",
                {
                    "processed_locally": False,
                    "intent_output": {
                        "continue_conversation": False,
                        "response": {
                            "response_type": "action_done",
                            "speech": {"plain": {"speech": answer}},
                        },
                    },
                },
                "2026-07-12T10:00:03.000000+00:00",
            )
        )
    events.append(make_event("run-end", None, "2026-07-12T10:00:04.000000+00:00"))
    return events


def install_debug_store(hass, runs_by_pipeline: dict[str, dict[str, Any]]) -> None:
    """Install a fake assist_pipeline debug store on the hass mock.

    ``runs_by_pipeline`` maps pipeline_id -> {run_id: (timestamp, events)}.
    """
    debug: dict[str, dict[str, SimpleNamespace]] = {}
    for pipeline_id, runs in runs_by_pipeline.items():
        debug[pipeline_id] = {
            run_id: SimpleNamespace(timestamp=stamp, events=events)
            for run_id, (stamp, events) in runs.items()
        }
    hass.data["assist_pipeline"] = SimpleNamespace(pipeline_debug=debug)
