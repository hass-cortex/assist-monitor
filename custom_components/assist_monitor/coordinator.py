"""Coordinator: scans the assist_pipeline debug store and publishes per-scope views.

Monitors EVERY assist (all pipelines / all satellites). It scans the debug store on a
safety-net interval and ALSO refreshes (with a short debounce) whenever any
assist_satellite entity changes state, so each new conversation updates incrementally in
near real time.

All scope semantics live HERE: ``data`` maps scope -> view, where a scope is either
``SCOPE_LATEST`` (the globally-newest conversation) or an Assist pipeline id, and a view is
the ONE dict sensors read — the flattened run (when the scope has one) merged with
``conversation_count`` and ``round_key``. A scope whose pipeline is idle (or gone) still gets
a count-only view, so the Conversations sensor keeps showing its cumulative total.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.assist_pipeline import (
    PipelineNotFound,
    async_get_pipeline,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import TrackStates, async_track_state_change_filtered
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    POLL_SECONDS,
    REFRESH_DEBOUNCE_SECONDS,
    SATELLITE_DOMAIN,
    SCOPE_LATEST,
)
from .counter import ConversationCounter
from .pipeline_reader import parse_all, round_key

_LOGGER = logging.getLogger(__name__)

type AssistMonitorConfigEntry = ConfigEntry[AssistMonitorCoordinator]


class AssistMonitorCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls the pipeline debug store; assist_satellite state changes trigger an instant refresh.

    ``data`` is ``{scope: <view>}`` — see the module docstring for the view contract.
    Runs are de-duplicated by value, so sensors only change when a conversation does.
    """

    def __init__(self, hass: HomeAssistant, entry: AssistMonitorConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_SECONDS),
            # The default request_refresh debouncer (10s cooldown) would coalesce a whole
            # voice round into one late refresh; a short cooldown keeps the event path
            # genuinely instant while still merging bursts of satellite state changes.
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=REFRESH_DEBOUNCE_SECONDS, immediate=True
            ),
        )
        self.entry = entry
        self.counter = ConversationCounter(hass)
        # Integration version from manifest.json, set at setup; shown as DeviceInfo.sw_version.
        self.version: str | None = None

    @callback
    def attach_listeners(self) -> None:
        """Refresh on any assist_satellite state change (incl. satellites added later).

        Domain-filtered tracking: HA's state-change index dispatches only assist_satellite
        events here, instead of this integration inspecting every state change on the bus.
        Cleanup is owned by the config entry, so it also runs if setup fails mid-way.
        """
        tracker = async_track_state_change_filtered(
            self.hass,
            TrackStates(False, set(), {SATELLITE_DOMAIN}),
            self._on_satellite_state_change,
        )
        self.entry.async_on_unload(tracker.async_remove)

    @callback
    def _on_satellite_state_change(self, _event: Event) -> None:
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Build the per-scope views. Fast in-memory scan/parse; safe on the event loop.

        ``round_key`` rides inside the view on purpose: the round_key SENSOR is registered
        last (see sensor.ROUND_KEY_SENSOR), so on an ordered HA->device state stream it
        emits after every other field of the round — a consumer subscribing to just that
        one sensor gets race-free round aggregation. The key's phase also encodes
        satellite-ness in-band (see pipeline_reader.round_key for the q/a/e/x phase table),
        so a satellite-only display can filter on the key alone. ``<n>`` in the key is the
        cumulative conversation count: stable within a round, persisted across restarts.
        """
        try:
            parsed = parse_all(self.hass)
        except Exception as err:
            # The debug store is a private assist_pipeline structure; if its shape changes,
            # UpdateFailed gets core's rate-limited logging instead of a traceback per poll.
            raise UpdateFailed(
                f"Reading the assist_pipeline debug store failed: {err}"
            ) from err
        runs: dict[str, dict[str, Any] | None] = {
            SCOPE_LATEST: parsed["latest"],
            **parsed["by_pipeline"],
        }
        counts = self.counter.observe(
            {scope: (run or {}).get("run_id") for scope, run in runs.items()}
        )
        views: dict[str, dict[str, Any]] = {}
        for scope in runs.keys() | counts.keys():
            run = runs.get(scope)
            view = dict(run) if run else {}
            if run:
                self._annotate(view)
            count = counts.get(scope)
            view["conversation_count"] = count
            view["round_key"] = round_key(run, count)
            views[scope] = view
        return views

    @callback
    def _annotate(self, view: dict[str, Any]) -> None:
        """Resolve the assist (pipeline) name, and the triggering device's name + area."""
        device_registry = dr.async_get(self.hass)
        area_registry = ar.async_get(self.hass)
        entity_registry = er.async_get(self.hass)

        pipeline_name = None
        if pipeline_id := view.get("pipeline_id"):
            try:
                pipeline_name = async_get_pipeline(self.hass, pipeline_id).name
            except KeyError, PipelineNotFound:  # removed pipeline / store not loaded
                pipeline_name = None
        view["pipeline_name"] = pipeline_name

        # Prefer the triggering device_id (only present from intent-start). Fall back to the
        # satellite's device (satellite_id is present from run-start) so the value is stable for
        # the whole run instead of flickering to None during the stt-end -> intent-start window.
        device_id = view.get("device_id")
        device = device_registry.async_get(device_id) if device_id else None
        if device is None and (satellite_id := view.get("satellite_id")):
            entity = entity_registry.async_get(satellite_id)
            if entity is not None and entity.device_id:
                device = device_registry.async_get(entity.device_id)
        if device is None:
            view["device_name"], view["area"] = None, None
            return
        area_name = None
        area_id = dr.async_get_effective_area_id(self.hass, device)
        if area_id and (area := area_registry.async_get_area(area_id)):
            area_name = area.name
        view["device_name"], view["area"] = (
            device.name_by_user or device.name,
            area_name,
        )
