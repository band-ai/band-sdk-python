"""Minimal adapters shared by Agent compositor tests."""

from __future__ import annotations

from band.core.simple_adapter import SimpleAdapter
from band.core.types import Capability


class MinimalAdapter(SimpleAdapter):
    """A SimpleAdapter that keeps every default hook."""

    async def on_message(self, *args, **kwargs) -> None:
        pass


class FilesAdapter(SimpleAdapter):
    """Bare SimpleAdapter declaring only Capability.FILES support."""

    SUPPORTED_CAPABILITIES = frozenset({Capability.FILES})

    async def on_started(self, agent_name, agent_description) -> None:
        pass

    async def on_message(self, *args, **kwargs) -> None:
        pass
