"""Assist Monitor: expose the latest Assist pipeline conversation as Home Assistant sensors.

Reads the assist_pipeline in-memory debug store (see coordinator.py for the data flow and the
per-scope view contract) and publishes one device for the globally-latest conversation plus one
device per Assist pipeline (see sensor.py).
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .coordinator import AssistMonitorConfigEntry, AssistMonitorCoordinator

__all__ = ["AssistMonitorConfigEntry", "AssistMonitorCoordinator"]

PLATFORMS = ["sensor"]


async def async_setup_entry(
    hass: HomeAssistant, entry: AssistMonitorConfigEntry
) -> bool:
    """Set up Assist Monitor from a config entry."""
    coordinator = AssistMonitorCoordinator(hass, entry)
    integration = await async_get_integration(hass, DOMAIN)
    # str(): .version is an AwesomeVersion; device registry compares sw_version against
    # None with AwesomeVersion.__eq__, which raises and kills every entity add.
    coordinator.version = str(integration.version)
    # Restore cumulative counts before the first refresh.
    await coordinator.counter.restore()
    await coordinator.async_config_entry_first_refresh()
    coordinator.attach_listeners()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AssistMonitorConfigEntry
) -> bool:
    """Unload a config entry (listeners are released via entry.async_on_unload)."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
