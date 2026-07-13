"""Tests for the Assist Monitor config flow."""

from __future__ import annotations

import pytest
from homeassistant.data_entry_flow import AbortFlow

from custom_components.assist_monitor.config_flow import AssistMonitorConfigFlow
from custom_components.assist_monitor.const import DOMAIN


async def test_user_step_shows_form() -> None:
    """First call (no input) shows the confirmation form."""
    flow = AssistMonitorConfigFlow()
    result = await flow.async_step_user(None)
    assert result["type"] == "form"
    assert result["step_id"] == "user"


async def test_user_step_creates_entry() -> None:
    """Submitting the form creates a single entry with no data."""
    flow = AssistMonitorConfigFlow()
    result = await flow.async_step_user({})
    assert result["type"] == "create_entry"
    assert result["title"] == "Assist Monitor"
    assert result["data"] == {}
    assert flow._unique_id == DOMAIN


async def test_user_step_aborts_on_duplicate() -> None:
    """A second instance aborts (single-instance integration)."""
    AssistMonitorConfigFlow.existing_unique_ids = {DOMAIN}
    flow = AssistMonitorConfigFlow()
    with pytest.raises(AbortFlow):
        await flow.async_step_user({})
