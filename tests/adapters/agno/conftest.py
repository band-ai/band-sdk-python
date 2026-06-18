"""Shared fixtures for the Agno adapter tests.

(``sample_platform_message`` comes from the root ``tests/conftest.py``.)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
from agno.agent import Agent as AgnoAgent
from agno.run.agent import RunOutput

from band.adapters.agno import AgnoAdapter
from band.core.types import AdapterFeatures, PlatformMessage
from band.testing import FakeAgentTools

from tests.adapters.agno.helpers import CapturingModel, SchemaTools


@pytest.fixture
def tools() -> FakeAgentTools:
    """A fresh, call-tracking Band tool surface for one test."""
    return FakeAgentTools()


@pytest.fixture
def make_agno_agent() -> Callable[..., tuple[MagicMock, MagicMock]]:
    """Factory returning ``(source_agent, copied_agent)`` fakes.

    ``deep_copy()`` returns the copy, mirroring how the adapter runs against a
    copy of the developer's agent. The copy's ``arun`` yields ``response``.
    """

    def _make(
        *,
        update_memory_on_run: bool = False,
        enable_agentic_memory: bool = False,
        response: RunOutput | None = None,
    ) -> tuple[MagicMock, MagicMock]:
        source = MagicMock(name="source_agent")
        source.update_memory_on_run = update_memory_on_run
        source.enable_agentic_memory = enable_agentic_memory

        copy = MagicMock(name="copied_agent")
        copy.add_tool = MagicMock()
        # Real Agno agents default additional_context to None; mirror that.
        copy.additional_context = None
        copy.arun = AsyncMock(
            return_value=response if response is not None else RunOutput()
        )
        source.deep_copy = MagicMock(return_value=copy)
        return source, copy

    return _make


@pytest.fixture
def make_started_adapter(
    make_agno_agent: Callable[..., tuple[MagicMock, MagicMock]],
) -> Callable[..., Awaitable[tuple[AgnoAdapter, MagicMock]]]:
    """Factory building an adapter past ``on_started``; returns
    ``(adapter, copied_agent)``."""

    async def _make(
        response: RunOutput | None = None,
        *,
        features: AdapterFeatures | None = None,
    ) -> tuple[AgnoAdapter, MagicMock]:
        source, copy = make_agno_agent(response=response)
        adapter = AgnoAdapter(source, features=features)
        await adapter.on_started("TestBot", "desc")
        return adapter, copy

    return _make


@pytest.fixture
def run_real_agent() -> Callable[..., Awaitable[CapturingModel]]:
    """Factory that drives one bootstrap turn through a real Agno agent and a
    capturing model, returning the model so a test can inspect the system prompt
    Agno actually assembled and sent."""

    async def _run(
        msg: PlatformMessage,
        *,
        instructions: str = "You are Dev.",
        additional_context: str | None = None,
        features: AdapterFeatures | None = None,
    ) -> CapturingModel:
        agno = AgnoAgent(
            model=CapturingModel(),
            instructions=instructions,
            additional_context=additional_context,
        )
        adapter = AgnoAdapter(agno, features=features)
        await adapter.on_started("Bot", "desc")
        await adapter.on_message(
            msg,
            SchemaTools([]),
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id=msg.room_id,
        )
        assert adapter.agent is not None
        model = adapter.agent.model
        assert isinstance(model, CapturingModel)
        return model

    return _run
