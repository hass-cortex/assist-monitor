"""Cumulative per-scope conversation counting, persisted so counts never reset.

Owns the counting invariants: each conversation (run_id) is counted at most once per
scope, and the counts AND the per-scope last-seen run_id are persisted together — so a
config-entry reload (which keeps the in-memory debug store alive) does not re-count the
current conversation.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, SCOPE_LATEST

_STORAGE_VERSION = 1

# Scope key used for the global count before the per-scope view refactor.
_LEGACY_GLOBAL_SCOPE = "__global__"


class ConversationCounter:
    """Counts each conversation once per scope; cumulative and persisted."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._counts: dict[str, int] = {}
        self._seen: dict[str, str] = {}  # scope -> last counted run_id
        self._store: Store = Store(hass, _STORAGE_VERSION, f"{DOMAIN}_counts")

    async def restore(self) -> None:
        """Load the persisted counts + last-seen run_ids (they survive restarts)."""
        data = await self._store.async_load()
        if isinstance(data, dict) and isinstance(data.get("counts"), dict):
            self._counts = {str(k): int(v) for k, v in data["counts"].items()}
        if isinstance(data, dict) and isinstance(data.get("seen"), dict):
            self._seen = {str(k): str(v) for k, v in data["seen"].items()}
        if _LEGACY_GLOBAL_SCOPE in self._counts:
            self._counts[SCOPE_LATEST] = self._counts.pop(_LEGACY_GLOBAL_SCOPE)
        if _LEGACY_GLOBAL_SCOPE in self._seen:
            self._seen[SCOPE_LATEST] = self._seen.pop(_LEGACY_GLOBAL_SCOPE)

    def observe(self, run_ids: dict[str, str | None]) -> dict[str, int]:
        """Bump each scope whose current run_id is new, then return a counts snapshot.

        The snapshot includes every persisted scope, not just the observed ones, so a
        scope's count stays visible while it is idle.
        """
        changed = False
        for scope, run_id in run_ids.items():
            if run_id and self._seen.get(scope) != run_id:
                self._seen[scope] = run_id
                self._counts[scope] = self._counts.get(scope, 0) + 1
                changed = True
        if changed:
            self._store.async_delay_save(
                lambda: {"counts": self._counts, "seen": self._seen}, 5
            )
        return dict(self._counts)
