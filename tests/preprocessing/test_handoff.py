"""Context commitment follows consumption, independently of error disposition."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.agent import Agent
from band.core.handoff import context_handoff
from band.preprocessing.default import DefaultPreprocessor
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_message_event
from tests.preprocessing.handoffkit import (
    HandoffAdapter,
    RecordingPreprocessor,
    UnclassifiedAdapter,
)


class TestContextHandoff:
    @pytest.mark.parametrize("phase", ["conversion", "setup", "provider"])
    @pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
    async def test_commitment_and_original_exception_survive_failure_or_cancellation(
        self, phase: str, error_type: type[BaseException]
    ) -> None:
        error = error_type("boundary failed")
        adapter = HandoffAdapter(phase, error)
        preprocessor = RecordingPreprocessor()
        agent = Agent(
            runtime=MagicMock(agent_id="agent-1"),
            adapter=adapter,
            preprocessor=preprocessor,
        )
        ctx = ExecutionContext(
            "room-1",
            MagicMock(),
            AsyncMock(),
            config=SessionConfig(enable_context_hydration=False),
        )
        ctx.set_participants([{"id": "user-1", "name": "Alice", "type": "User"}])
        ctx.inject_system_message("First contact update")
        event = make_message_event(room_id="room-1")

        with pytest.raises(error_type) as failure:
            await agent._on_execute(ctx, event)

        assert failure.value is error
        consumed = phase == "provider"
        assert preprocessor.commits == int(consumed)
        assert ctx.is_llm_initialized is consumed
        assert ctx.participants_changed() is not consumed
        assert ctx.peek_pending_system_messages() == (
            [] if consumed else ["First contact update"]
        )

        ctx.set_participants([{"id": "user-1", "name": "Alicia", "type": "User"}])
        ctx.inject_system_message("Second contact update")
        adapter.error = None

        async def concurrent_update() -> None:
            ctx.set_participants([{"id": "user-1", "name": "Ally", "type": "User"}])
            ctx.inject_system_message("Third contact update")

        adapter.during_execution = concurrent_update
        await agent._on_execute(ctx, event)
        accepted = adapter.received[-1]
        assert accepted.bootstrap is not consumed
        assert accepted.participants is not None and "Alicia" in accepted.participants
        assert accepted.contacts == (
            "Second contact update"
            if consumed
            else "First contact update\nSecond contact update"
        )
        assert preprocessor.commits == int(consumed) + 1

        following = await DefaultPreprocessor().process(ctx, event, "agent-1")
        assert following is not None
        assert not following.is_session_bootstrap
        assert (
            following.participants_msg is not None
            and "Ally" in following.participants_msg
        )
        assert following.contacts_msg == "Third contact update"

    async def test_child_setup_failure_cannot_change_parent_commitment(self) -> None:
        async def child() -> None:
            with pytest.raises(ValueError), context_handoff():
                raise ValueError("child setup failed")

        adapter = UnclassifiedAdapter()

        async def finish_child_during_delivery() -> None:
            await asyncio.create_task(child())

        adapter.during_execution = finish_child_during_delivery
        preprocessor = RecordingPreprocessor()
        agent = Agent(
            runtime=MagicMock(agent_id="agent-1"),
            adapter=adapter,
            preprocessor=preprocessor,
        )
        ctx = ExecutionContext(
            "room-1",
            MagicMock(),
            AsyncMock(),
            config=SessionConfig(enable_context_hydration=False),
        )
        await agent._on_execute(ctx, make_message_event(room_id="room-1"))
        assert preprocessor.commits == 1
        assert ctx.is_llm_initialized

    async def test_nested_delivery_restores_outer_commitment(self) -> None:
        inner_preprocessor = RecordingPreprocessor()
        inner_agent = Agent(
            runtime=MagicMock(agent_id="inner"),
            adapter=HandoffAdapter(error=ValueError("inner setup failed")),
            preprocessor=inner_preprocessor,
        )
        inner_ctx = ExecutionContext(
            "inner-room",
            MagicMock(),
            AsyncMock(),
            config=SessionConfig(enable_context_hydration=False),
        )
        outer_adapter = HandoffAdapter()

        async def nested_delivery() -> None:
            with pytest.raises(ValueError, match="inner setup failed"):
                await inner_agent._on_execute(
                    inner_ctx, make_message_event(room_id="inner-room")
                )

        outer_adapter.during_execution = nested_delivery
        outer_preprocessor = RecordingPreprocessor()
        outer_agent = Agent(
            runtime=MagicMock(agent_id="outer"),
            adapter=outer_adapter,
            preprocessor=outer_preprocessor,
        )
        outer_ctx = ExecutionContext(
            "outer-room",
            MagicMock(),
            AsyncMock(),
            config=SessionConfig(enable_context_hydration=False),
        )
        await outer_agent._on_execute(
            outer_ctx, make_message_event(room_id="outer-room")
        )
        assert inner_preprocessor.commits == 0
        assert not inner_ctx.is_llm_initialized
        assert outer_preprocessor.commits == 1
        assert outer_ctx.is_llm_initialized

    async def test_later_setup_failure_cannot_undo_a_consumed_handoff(self) -> None:
        adapter = HandoffAdapter()

        async def later_setup() -> None:
            with context_handoff():
                raise ValueError("secondary setup failed")

        adapter.during_execution = later_setup
        preprocessor = RecordingPreprocessor()
        agent = Agent(
            runtime=MagicMock(agent_id="agent-1"),
            adapter=adapter,
            preprocessor=preprocessor,
        )
        ctx = ExecutionContext(
            "room-1",
            MagicMock(),
            AsyncMock(),
            config=SessionConfig(enable_context_hydration=False),
        )
        with pytest.raises(ValueError, match="secondary setup failed"):
            await agent._on_execute(ctx, make_message_event(room_id="room-1"))
        assert preprocessor.commits == 1
        assert ctx.is_llm_initialized
