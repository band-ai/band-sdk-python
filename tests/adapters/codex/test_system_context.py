"""System context survives unfinished turns until a native turn completes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

import pytest

from band.adapters.codex import SYSTEM_CONTEXT_PREFIX, CodexAdapter
from band.agent import Agent
from band.core.protocols import TurnResultAlreadyReported
from band.core.types import AgentInput
from band.integrations.codex import CodexJsonRpcError
from band.integrations.codex.types import CodexRequestMethod, CodexTurnStatus
from band.preprocessing.default import DefaultPreprocessor
from band.runtime.execution import ExecutionContext
from band.runtime.formatters import build_participants_message
from tests.adapters.codex.kit import (
    ROOM_ID,
    ToolSchemaFakeTools,
    answered_turn,
)
from tests.adapters.codexturns import FakeCodexClient, turn_completed
from tests.conftest import make_message_event

FIRST_UPDATE = "First contact update"
SECOND_UPDATE = "Second contact update"


async def system_input(context: ExecutionContext, content: str) -> AgentInput:
    """Prepare the runtime's context snapshot with a fake Band transport boundary."""
    event = make_message_event(room_id=context.room_id, msg_id=content, content=content)
    inp = await DefaultPreprocessor().process(context, event, "agent-1")
    assert inp is not None
    return replace(inp, tools=ToolSchemaFakeTools())


def system_contexts(turn: dict[str, Any]) -> list[str]:
    """Return the system context passed to a native turn."""
    return [
        item["text"].removeprefix(f"{SYSTEM_CONTEXT_PREFIX} ")
        for item in turn["input"]
        if item["text"].startswith(SYSTEM_CONTEXT_PREFIX)
    ]


class TestSystemContext:
    async def test_cancelled_seed_keeps_committed_and_concurrent_updates_once(
        self,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
        system_context: Callable[[str], ExecutionContext],
    ) -> None:
        client = FakeCodexClient()
        adapter = await started_codex(client)
        context = system_context(ROOM_ID)
        context.inject_system_message(FIRST_UPDATE)
        first = await system_input(context, "first")

        seeding = asyncio.create_task(
            Agent._commit_unless_refused(first, adapter.on_event(first))
        )
        await client.turn_started.wait()
        context.inject_system_message(SECOND_UPDATE)
        seeding.cancel()
        with pytest.raises(asyncio.CancelledError):
            await seeding
        await adapter.backend_turns.settled(ROOM_ID)
        client.script(*answered_turn("turn-2"), *answered_turn("turn-3"))
        successor = await system_input(context, "successor")
        assert successor.contacts_msg == SECOND_UPDATE
        await Agent._commit_unless_refused(successor, adapter.on_event(successor))
        later = await system_input(context, "later")
        await Agent._commit_unless_refused(later, adapter.on_event(later))

        first_turn, retried, following = client.params_of(CodexRequestMethod.TURN_START)
        assert first_turn["threadId"] != retried["threadId"]
        assert system_contexts(retried) == [
            build_participants_message(context.participants),
            f"{FIRST_UPDATE}\n{SECOND_UPDATE}",
        ]
        assert system_contexts(following) == []

    async def test_a_failed_seed_keeps_context_until_a_successor_completes(
        self,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
        system_context: Callable[[str], ExecutionContext],
    ) -> None:
        client = FakeCodexClient(
            events=[turn_completed("turn-1", status=CodexTurnStatus.FAILED)]
        )
        adapter = await started_codex(client)
        context = system_context(ROOM_ID)
        context.inject_system_message(FIRST_UPDATE)
        failed = await system_input(context, "failure")
        with pytest.raises(TurnResultAlreadyReported):
            await Agent._commit_unless_refused(failed, adapter.on_event(failed))
        client.script(*answered_turn("turn-2"), *answered_turn("turn-3"))
        retry = await system_input(context, "retry")
        await Agent._commit_unless_refused(retry, adapter.on_event(retry))
        later = await system_input(context, "later")
        await Agent._commit_unless_refused(later, adapter.on_event(later))

        first, retried, following = client.params_of(CodexRequestMethod.TURN_START)
        assert first["threadId"] != retried["threadId"]
        assert system_contexts(retried) == [
            build_participants_message(context.participants),
            FIRST_UPDATE,
        ]
        assert system_contexts(following) == []

    async def test_a_seeded_threads_failed_context_retries_until_completion(
        self,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
        system_context: Callable[[str], ExecutionContext],
    ) -> None:
        client = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(client)
        context = system_context(ROOM_ID)
        initial = await system_input(context, "initial")
        await Agent._commit_unless_refused(initial, adapter.on_event(initial))
        context.inject_system_message(FIRST_UPDATE)
        client.script(turn_completed("turn-2", status=CodexTurnStatus.FAILED))
        failed = await system_input(context, "failure")
        with pytest.raises(TurnResultAlreadyReported):
            await Agent._commit_unless_refused(failed, adapter.on_event(failed))
        client.script(*answered_turn("turn-3"), *answered_turn("turn-4"))
        retry = await system_input(context, "retry")
        await Agent._commit_unless_refused(retry, adapter.on_event(retry))
        later = await system_input(context, "later")
        await Agent._commit_unless_refused(later, adapter.on_event(later))

        *_, retried, following = client.params_of(CodexRequestMethod.TURN_START)
        assert system_contexts(retried) == [FIRST_UPDATE]
        assert system_contexts(following) == []

    async def test_context_survives_a_thread_setup_failure_before_prompting(
        self,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
        system_context: Callable[[str], ExecutionContext],
    ) -> None:
        client = FakeCodexClient(
            events=answered_turn(),
            thread_start_error=CodexJsonRpcError(
                code=-32603, message="temporarily unavailable"
            ),
        )
        adapter = await started_codex(client)
        context = system_context(ROOM_ID)
        context.inject_system_message(FIRST_UPDATE)
        failed = await system_input(context, "failure")
        with pytest.raises(CodexJsonRpcError, match="temporarily unavailable"):
            await Agent._commit_unless_refused(failed, adapter.on_event(failed))
        retry = await system_input(context, "retry")
        await Agent._commit_unless_refused(retry, adapter.on_event(retry))

        (prompt,) = client.params_of(CodexRequestMethod.TURN_START)
        assert system_contexts(prompt) == [
            build_participants_message(context.participants),
            FIRST_UPDATE,
        ]

    async def test_room_cleanup_drops_only_that_rooms_pending_context(
        self,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
        system_context: Callable[[str], ExecutionContext],
    ) -> None:
        first_client = FakeCodexClient(
            events=[turn_completed("turn-1", status=CodexTurnStatus.FAILED)]
        )
        other_client = FakeCodexClient(events=answered_turn())
        rejoined_client = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(first_client, other_client, rejoined_client)
        first_context = system_context(ROOM_ID)
        first_context.inject_system_message(FIRST_UPDATE)
        failed = await system_input(first_context, "failure")
        with pytest.raises(TurnResultAlreadyReported):
            await Agent._commit_unless_refused(failed, adapter.on_event(failed))

        other_context = system_context("room-other")
        other = await system_input(other_context, "another room")
        await Agent._commit_unless_refused(other, adapter.on_event(other))
        await adapter.on_cleanup(ROOM_ID)
        rejoined_context = system_context(ROOM_ID)
        rejoined = await system_input(rejoined_context, "rejoined")
        await Agent._commit_unless_refused(rejoined, adapter.on_event(rejoined))

        (other_prompt,) = other_client.params_of(CodexRequestMethod.TURN_START)
        (rejoined_prompt,) = rejoined_client.params_of(CodexRequestMethod.TURN_START)
        assert system_contexts(other_prompt) == [
            build_participants_message(other_context.participants)
        ]
        assert system_contexts(rejoined_prompt) == [
            build_participants_message(rejoined_context.participants)
        ]
