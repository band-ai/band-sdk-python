"""Codex adapter: a silent turn is allowed longer while a tool it started runs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from pydantic import ValidationError

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.codex.types import CodexRequestMethod
from tests.adapters.codex.activity.kit import (
    COMPACTION,
    DOCS_LOOKUP,
    LIST_FILES,
    SUB_AGENT_WAIT,
    item_completed,
    item_started,
)
from tests.adapters.codex.kit import ROOM_ID, ToolSchemaFakeTools, answered_turn
from tests.adapters.codex.silentturn import (
    CEILING_S,
    IDLE_S,
    silence_guarded,
    silent_for,
)
from tests.adapters.codex.timedturn import (
    FIRST_TURN_ID,
    INTERRUPTED_FIRST_TURN,
    MARGIN_S,
    failures,
    running_turn,
)
from tests.adapters.codexturns import FIRST_THREAD_ID, FakeCodexClient

TOOL_IDLE_S = 900.0
TOOL_LIMIT = "turn_tool_idle_timeout_s"
# A tool's silence between the idle limit and the tool allowance.
QUIET_TOOL_S = 600.0
SECOND_TURN_ID = "turn-2"


def tool_patient() -> CodexAdapterConfig:
    """Allow ``IDLE_S`` of silence, and ``TOOL_IDLE_S`` while a tool runs."""
    return silence_guarded(turn_tool_idle_timeout_s=TOOL_IDLE_S)


@pytest.mark.looptime
class TestTurnToolSilence:
    @pytest.mark.parametrize(
        "tool",
        [LIST_FILES, DOCS_LOOKUP, COMPACTION, SUB_AGENT_WAIT],
        ids=["command", "mcp-tool", "context-compaction", "delegated-work"],
    )
    async def test_a_tool_silent_past_the_idle_limit_is_left_running(
        self,
        tool: dict[str, Any],
        started_rooms: Callable[..., Awaitable[CodexAdapter]],
    ) -> None:
        """An open item of the turn says what it is working on: a command, a
        tool call, a context compaction, which is one long model call, or the
        wait for a sub-agent."""
        client = FakeCodexClient()
        adapter = await started_rooms({ROOM_ID: client}, tool_patient())
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools) as turn:
            await client.turn_started.wait()
            client.emit_turn(FIRST_TURN_ID, item_started(tool))
            await asyncio.sleep(QUIET_TOOL_S)
            assert not turn.done()
            assert failures(tools) == []

            client.emit_turn(FIRST_TURN_ID, item_completed(tool), *answered_turn())
            await turn

        assert failures(tools) == []
        assert client.params_of(CodexRequestMethod.TURN_INTERRUPT) == []

    async def test_a_command_silent_past_the_tool_allowance_is_interrupted(
        self, started_rooms: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        client = FakeCodexClient()
        adapter = await started_rooms({ROOM_ID: client}, tool_patient())
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools) as turn:
            await client.turn_started.wait()
            client.emit_turn(FIRST_TURN_ID, item_started(LIST_FILES))
            await asyncio.sleep(TOOL_IDLE_S - MARGIN_S)
            assert not turn.done()
            assert failures(tools) == []

            await asyncio.sleep(2 * MARGIN_S)
            assert failures(tools) == silent_for(TOOL_IDLE_S, TOOL_LIMIT)
            with pytest.raises(TurnResultAlreadyReported):
                await turn

        assert (
            client.params_of(CodexRequestMethod.TURN_INTERRUPT)
            == INTERRUPTED_FIRST_TURN
        )

    async def test_the_idle_limit_applies_again_once_the_command_completes(
        self, started_rooms: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        client = FakeCodexClient()
        adapter = await started_rooms({ROOM_ID: client}, tool_patient())
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools) as turn:
            await client.turn_started.wait()
            client.emit_turn(FIRST_TURN_ID, item_started(LIST_FILES))
            await asyncio.sleep(QUIET_TOOL_S)
            client.emit_turn(FIRST_TURN_ID, item_completed(LIST_FILES))
            await asyncio.sleep(IDLE_S - MARGIN_S)
            assert not turn.done()
            assert failures(tools) == []

            await asyncio.sleep(2 * MARGIN_S)
            assert failures(tools) == silent_for(IDLE_S)
            with pytest.raises(TurnResultAlreadyReported):
                await turn

    async def test_without_a_tool_allowance_a_silent_command_gets_the_idle_limit(
        self, started_rooms: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        client = FakeCodexClient()
        adapter = await started_rooms({ROOM_ID: client}, silence_guarded())
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools) as turn:
            await client.turn_started.wait()
            client.emit_turn(FIRST_TURN_ID, item_started(LIST_FILES))
            await asyncio.sleep(IDLE_S - MARGIN_S)
            assert not turn.done()
            assert failures(tools) == []

            await asyncio.sleep(2 * MARGIN_S)
            assert failures(tools) == silent_for(IDLE_S)
            with pytest.raises(TurnResultAlreadyReported):
                await turn

    async def test_a_command_an_earlier_turn_left_open_earns_no_allowance(
        self, started_rooms: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """Codex closes an item only with its item/completed, so a command can
        outlive the turn that started it."""
        client = FakeCodexClient(events=[item_started(LIST_FILES), *answered_turn()])
        adapter = await started_rooms({ROOM_ID: client}, tool_patient())
        earlier, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()
        async with running_turn(adapter, earlier) as left_open:
            await left_open
        client.turn_started.clear()

        async with running_turn(adapter, tools) as turn:
            await client.turn_started.wait()
            await asyncio.sleep(IDLE_S + MARGIN_S)
            assert failures(tools) == silent_for(IDLE_S)
            with pytest.raises(TurnResultAlreadyReported):
                await turn

        assert client.params_of(CodexRequestMethod.TURN_INTERRUPT) == [
            {"threadId": FIRST_THREAD_ID, "turnId": SECOND_TURN_ID}
        ]

    @pytest.mark.parametrize(
        ("seconds", "refusal"),
        [
            pytest.param(0.0, "greater_than", id="zero"),
            pytest.param(-1.0, "greater_than", id="negative"),
            pytest.param(float("inf"), "finite_number", id="infinite"),
            pytest.param(float("nan"), "finite_number", id="not-a-number"),
        ],
    )
    async def test_a_tool_allowance_that_cannot_bound_a_wait_is_refused(
        self, seconds: float, refusal: str
    ) -> None:
        with pytest.raises(ValidationError) as refused:
            silence_guarded(turn_tool_idle_timeout_s=seconds)

        assert [(error["loc"], error["type"]) for error in refused.value.errors()] == [
            ((TOOL_LIMIT,), refusal)
        ]

    async def test_a_tool_allowance_without_an_idle_limit_is_refused(self) -> None:
        """Alone it would never apply, which its operator could not see."""
        with pytest.raises(ValidationError, match="turn_idle_timeout_s") as refused:
            CodexAdapterConfig(turn_tool_idle_timeout_s=TOOL_IDLE_S)

        assert [(error["loc"], error["type"]) for error in refused.value.errors()] == [
            ((), "value_error")
        ]

    async def test_a_tool_allowance_below_the_idle_limit_is_refused(self) -> None:
        """It is the longer of the two: a shorter one would stop a running tool
        sooner than a quiet model."""
        with pytest.raises(ValidationError, match=TOOL_LIMIT) as refused:
            silence_guarded(turn_tool_idle_timeout_s=IDLE_S - 1)

        assert [(error["loc"], error["type"]) for error in refused.value.errors()] == [
            ((), "value_error")
        ]

    @pytest.mark.parametrize(
        "allowance_s",
        [IDLE_S, CEILING_S, 2 * CEILING_S],
        ids=["equal-to-the-idle-limit", "equal-to-the-total", "above-the-total"],
    )
    async def test_a_tool_allowance_from_the_idle_limit_up_is_accepted(
        self, allowance_s: float
    ) -> None:
        """At or above the total limit it says: never stop a running tool for
        silence."""
        config = silence_guarded(turn_tool_idle_timeout_s=allowance_s)

        assert config.turn_tool_idle_timeout_s == allowance_s
