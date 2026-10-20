from __future__ import annotations

import warnings

import pytest

from band.adapters.agno import (
    AgnoAdapter,
)
from band.core.types import Capability


class TestMemoryCollisionWarning:
    """Collision is detected against the runtime agent at startup, not __init__."""

    async def test_warns_on_update_memory_on_run_with_memory_capability(
        self, make_agno_agent
    ):
        agent = make_agno_agent(update_memory_on_run=True)
        adapter = AgnoAdapter(agent=agent, capabilities=Capability.MEMORY)

        with pytest.warns(UserWarning, match="update_memory_on_run"):
            await adapter.on_started("TestBot", "desc")

    async def test_warns_on_agentic_memory_with_memory_capability(
        self, make_agno_agent
    ):
        agent = make_agno_agent(enable_agentic_memory=True)
        adapter = AgnoAdapter(agent=agent, capabilities=Capability.MEMORY)

        with pytest.warns(UserWarning, match="enable_agentic_memory"):
            await adapter.on_started("TestBot", "desc")

    async def test_no_warning_without_memory_capability(self, make_agno_agent):
        agent = make_agno_agent(update_memory_on_run=True, enable_agentic_memory=True)
        adapter = AgnoAdapter(agent=agent)  # no MEMORY capability -> no collision

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            await adapter.on_started("TestBot", "desc")
