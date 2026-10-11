"""Context remains owed until an input can have reached the provider."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.agent import Agent
from band.core.exceptions import InputNotAccepted
from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput, PlatformMessage
from band.preprocessing.default import DefaultPreprocessor
from band.runtime.execution import ExecutionContext
from band.runtime.types import SessionConfig
from tests.preprocessing.test_default import make_message_event


class ContextAdapter(SimpleAdapter[Any]):
    def __init__(self) -> None:
        super().__init__()
        self.inputs: list[AgentInput] = []
        self.error: Exception | None = InputNotAccepted("occupied")
        self.during_attempt: Callable[[], None] = lambda: None

    async def on_event(self, inp: AgentInput) -> None:
        self.inputs.append(inp)
        self.during_attempt()
        if self.error is not None:
            raise self.error

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: Any,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        raise AssertionError("on_event owns this test boundary")

    async def on_cleanup(self, room_id: str) -> None:
        pass


@pytest.fixture
def context() -> ExecutionContext:
    ctx = ExecutionContext(
        "room-1",
        MagicMock(),
        AsyncMock(),
        config=SessionConfig(enable_context_hydration=False),
    )
    ctx.set_participants([{"id": "user-1", "name": "Alice", "type": "User"}])
    ctx.inject_system_message("First contact update")
    return ctx


async def test_rejected_context_is_refreshed_and_concurrent_updates_remain_owed(
    context: ExecutionContext,
) -> None:
    adapter = ContextAdapter()
    runtime = MagicMock(agent_id="agent-1")
    agent = Agent(runtime=runtime, adapter=adapter)
    event = make_message_event()
    with pytest.raises(InputNotAccepted):
        await agent._on_execute(context, event)

    assert not context.is_llm_initialized
    assert context.participants_changed()
    assert context.peek_pending_system_messages() == ["First contact update"]

    context.set_participants([{"id": "user-1", "name": "Alicia", "type": "User"}])
    context.inject_system_message("Second contact update")

    def concurrent_updates() -> None:
        context.set_participants([{"id": "user-1", "name": "Ally", "type": "User"}])
        context.inject_system_message("Third contact update")

    adapter.error = None
    adapter.during_attempt = concurrent_updates
    await agent._on_execute(context, event)
    accepted = adapter.inputs[-1]
    assert accepted.is_session_bootstrap
    assert accepted.participants_msg is not None
    assert "Alicia" in accepted.participants_msg
    assert accepted.contacts_msg == "First contact update\nSecond contact update"
    assert context.is_llm_initialized

    prepared = await DefaultPreprocessor().process(context, event, "agent-1")
    assert prepared is not None
    assert not prepared.is_session_bootstrap
    assert prepared.participants_msg is not None
    assert "Ally" in prepared.participants_msg
    assert prepared.contacts_msg == "Third contact update"
    assert prepared.commit_context is not None
    prepared.commit_context()
    assert not context.participants_changed()
    assert context.peek_pending_system_messages() == []


async def test_real_failure_conservatively_commits_prepared_context(
    context: ExecutionContext,
) -> None:
    adapter = ContextAdapter()
    adapter.error = ValueError("provider acceptance unknown")
    agent = Agent(runtime=MagicMock(agent_id="agent-1"), adapter=adapter)
    with pytest.raises(ValueError):
        await agent._on_execute(context, make_message_event())
    assert context.is_llm_initialized
    assert not context.participants_changed()
    assert context.peek_pending_system_messages() == []
