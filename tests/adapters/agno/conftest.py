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
def make_agno_agent() -> Callable[..., MagicMock]:
    """Factory returning a configured Agno agent fake.

    The adapter runs against this instance directly, so it carries the
    history/memory config the guards read and its ``arun`` yields ``response``.
    """

    def _make(
        *,
        update_memory_on_run: bool = False,
        enable_agentic_memory: bool = False,
        add_history_to_context: bool = False,
        db: object | None = None,
        response: RunOutput | None = None,
    ) -> MagicMock:
        agent = MagicMock(name="agno_agent")
        agent.update_memory_on_run = update_memory_on_run
        agent.enable_agentic_memory = enable_agentic_memory
        # Explicit falsy defaults: a bare MagicMock would expose these as truthy
        # auto-attributes and spuriously trip the history-management guard.
        agent.add_history_to_context = add_history_to_context
        agent.db = db
        agent.add_tool = MagicMock()
        # Real Agno agents default additional_context to None; mirror that.
        agent.additional_context = None
        # The adapter captures the user's tools at startup, then installs a
        # callable factory. A bare MagicMock `.tools` is itself callable and would
        # be mistaken for a user-supplied tools factory, so pin it to a list.
        agent.tools = []
        agent.arun = AsyncMock(
            return_value=response if response is not None else RunOutput()
        )
        return agent

    return _make


@pytest.fixture
def make_started_adapter(
    make_agno_agent: Callable[..., MagicMock],
) -> Callable[..., Awaitable[tuple[AgnoAdapter, MagicMock]]]:
    """Factory building an adapter past ``on_started``; returns
    ``(adapter, agent)``."""

    async def _make(
        response: RunOutput | None = None,
        *,
        features: AdapterFeatures | None = None,
        add_history_to_context: bool = False,
        db: object | None = None,
    ) -> tuple[AgnoAdapter, MagicMock]:
        agent = make_agno_agent(
            response=response,
            add_history_to_context=add_history_to_context,
            db=db,
        )
        adapter = AgnoAdapter(agent, features=features)
        await adapter.on_started("TestBot", "desc")
        return adapter, agent

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
