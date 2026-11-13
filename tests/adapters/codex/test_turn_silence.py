"""Codex adapter: a turn that goes silent is interrupted; a busy one is left alone."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from pydantic import ValidationError

from band.adapters.codex import CodexAdapter, CodexAdapterConfig, CodexCommand
from band.core.protocols import TurnResultAlreadyReported
from band.core.turn_lifecycle import BACKEND_CANCEL_TIMEOUT_SECONDS
from band.integrations.codex.types import CodexRequestMethod
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import TurnEffect
from tests.adapters.codex.kit import (
    ACCEPTED,
    PATIENT_APPROVAL_S,
    ROOM_ID,
    CodexRoom,
    StayQuietInput,
    ToolSchemaFakeTools,
    answered_turn,
    delegated_to,
    make_platform_message,
    parked_on_approval,
    send_bootstrap,
)
from tests.adapters.codex.silentturn import (
    CEILING_S,
    HEARD_EVERY_S,
    IDLE_S,
    silence_guarded,
    silent_for,
    sub_agent_thinking,
    thinking,
)
from tests.adapters.codex.timedturn import (
    FIRST_TURN_ID,
    INTERRUPTED_FIRST_TURN,
    MARGIN_S,
    failures,
    past_its_total,
    running_turn,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    final_text,
    tool_call_request,
    turn_completed,
)

SILENT_ROOM = "room-silent"
BUSY_ROOM = "room-busy"
SUB_AGENT_THREAD = "thr-sub"


@pytest.mark.looptime
class TestTurnSilence:
    async def test_a_turn_heard_from_every_minute_outlives_the_idle_limit(
        self, started_rooms: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """Half an hour of reasoning the room never sees is not silence."""
        client = FakeCodexClient()
        adapter = await started_rooms({ROOM_ID: client}, silence_guarded())
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools) as turn:
            await client.turn_started.wait()
            for _ in range(30):
                await asyncio.sleep(HEARD_EVERY_S)
                client.emit_turn(FIRST_TURN_ID, thinking())
            client.emit_turn(FIRST_TURN_ID, *answered_turn())
            await turn

        assert failures(tools) == []
        assert client.params_of(CodexRequestMethod.TURN_INTERRUPT) == []

    async def test_a_silent_turn_is_interrupted_at_the_idle_limit_and_not_before(
        self, started_rooms: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        client = FakeCodexClient()
        adapter = await started_rooms({ROOM_ID: client}, silence_guarded())
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools) as turn:
            await client.turn_started.wait()
            await asyncio.sleep(IDLE_S - MARGIN_S)
            assert not turn.done()
            assert failures(tools) == []

            await asyncio.sleep(2 * MARGIN_S)
            assert failures(tools) == silent_for(IDLE_S)
            with pytest.raises(TurnResultAlreadyReported):
                await turn

        assert failures(tools) == silent_for(IDLE_S)
        assert (
            client.params_of(CodexRequestMethod.TURN_INTERRUPT)
            == INTERRUPTED_FIRST_TURN
        )

    async def test_a_silent_turn_frees_its_room_when_the_interrupt_goes_unanswered(
        self, started_rooms: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """Silence ends a turn through the same bounded interrupt as the total
        limit: one failure, the process closed, and the next message answered."""
        client = FakeCodexClient()
        client.interrupt_released.clear()
        adapter = await started_rooms({ROOM_ID: client}, silence_guarded())
        silent, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()

        async with running_turn(adapter, silent) as turn:
            await client.turn_started.wait()
            await asyncio.sleep(IDLE_S + BACKEND_CANCEL_TIMEOUT_SECONDS - MARGIN_S)
            assert not turn.done()
            assert failures(silent) == []

            await asyncio.sleep(2 * MARGIN_S)
            assert failures(silent) == silent_for(IDLE_S)
            with pytest.raises(TurnResultAlreadyReported):
                await turn
        client.script(*answered_turn("turn-2"))
        await send_bootstrap(adapter, tools=tools)

        assert failures(silent) == silent_for(IDLE_S)
        assert client.closed
        assert failures(tools) == []

    async def test_each_event_restarts_the_silence_clock(
        self, started_rooms: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        heard_at_s = 100.0
        client = FakeCodexClient()
        adapter = await started_rooms({ROOM_ID: client}, silence_guarded())
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools) as turn:
            await client.turn_started.wait()
            await asyncio.sleep(heard_at_s)
            client.emit_turn(FIRST_TURN_ID, thinking())
            await asyncio.sleep(IDLE_S - MARGIN_S)
            assert not turn.done()
            assert failures(tools) == []

            await asyncio.sleep(2 * MARGIN_S)
            assert failures(tools) == silent_for(IDLE_S)
            with pytest.raises(TurnResultAlreadyReported):
                await turn

    async def test_a_sub_agent_that_is_heard_from_does_not_restart_the_clock(
        self, started_rooms: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """A sub-agent's notifications name its own thread and turn, so the
        turn that delegated to it is silent while only the sub-agent is heard."""
        client = FakeCodexClient()
        adapter = await started_rooms({ROOM_ID: client}, silence_guarded())
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools) as turn:
            await client.turn_started.wait()
            client.emit_turn(FIRST_TURN_ID, delegated_to(SUB_AGENT_THREAD))
            await asyncio.sleep(HEARD_EVERY_S)
            client.emit(sub_agent_thinking(SUB_AGENT_THREAD))
            await asyncio.sleep(IDLE_S - HEARD_EVERY_S - MARGIN_S)
            assert not turn.done()
            assert failures(tools) == []

            await asyncio.sleep(2 * MARGIN_S)
            assert failures(tools) == silent_for(IDLE_S)
            with pytest.raises(TurnResultAlreadyReported):
                await turn

    async def test_the_total_limit_still_ends_a_turn_that_is_never_silent(
        self, started_rooms: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        total_s = 600.0
        client = FakeCodexClient()
        adapter = await started_rooms(
            {ROOM_ID: client}, silence_guarded(turn_timeout_s=total_s)
        )
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools) as turn:
            await client.turn_started.wait()
            for _ in range(9):
                await asyncio.sleep(HEARD_EVERY_S)
                client.emit_turn(FIRST_TURN_ID, thinking())
            await asyncio.sleep(HEARD_EVERY_S - MARGIN_S)
            assert not turn.done()
            assert failures(tools) == []

            await asyncio.sleep(2 * MARGIN_S)
            assert failures(tools) == past_its_total(total_s)
            with pytest.raises(TurnResultAlreadyReported):
                await turn

        assert (
            client.params_of(CodexRequestMethod.TURN_INTERRUPT)
            == INTERRUPTED_FIRST_TURN
        )

    async def test_a_parked_approval_that_outlasts_the_idle_limit_is_not_silence(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(
            *parked_on_approval(*answered_turn()),
            approval_wait_timeout_s=PATIENT_APPROVAL_S,
            turn_idle_timeout_s=IDLE_S,
            turn_timeout_s=2 * PATIENT_APPROVAL_S,
        )

        await room.send("run tests")
        await asyncio.sleep(IDLE_S + MARGIN_S)
        assert not room.turn.done()
        assert await room.adapter.try_handle_control(
            make_platform_message(
                room_id=ROOM_ID,
                content=f"/{CodexCommand.APPROVE} {room.approval_token}",
            ),
            ToolSchemaFakeTools(),
        )
        await room.settled()

        assert failures(room.deliveries[0]) == []
        assert room.client.answers[10] == ACCEPTED
        assert room.client.params_of(CodexRequestMethod.TURN_INTERRUPT) == []

    async def test_a_band_tool_call_that_outlasts_the_idle_limit_is_not_silence(
        self, started_rooms: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        running, finish = asyncio.Event(), asyncio.Event()

        @declares_turn_effect(TurnEffect.DECLINE)
        async def stay_quiet(args: StayQuietInput) -> str:
            running.set()
            await finish.wait()
            return "quiet"

        client = FakeCodexClient(
            events=[
                tool_call_request(1, "stayquiet"),
                final_text("Nothing to add."),
                turn_completed(),
            ]
        )
        adapter = await started_rooms(
            {ROOM_ID: client},
            silence_guarded(),
            additional_tools=[(StayQuietInput, stay_quiet)],
        )
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools) as turn:
            await running.wait()
            await asyncio.sleep(IDLE_S + MARGIN_S)
            assert not turn.done()
            finish.set()
            await turn

        assert failures(tools) == []
        assert client.params_of(CodexRequestMethod.TURN_INTERRUPT) == []

    async def test_silence_in_one_room_leaves_a_busy_room_running(
        self, started_rooms: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        silent_client, busy_client = FakeCodexClient(), FakeCodexClient()
        adapter = await started_rooms(
            {SILENT_ROOM: silent_client, BUSY_ROOM: busy_client}, silence_guarded()
        )
        silent_tools, busy_tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()

        async with (
            running_turn(adapter, silent_tools, SILENT_ROOM) as silent_turn,
            running_turn(adapter, busy_tools, BUSY_ROOM) as busy_turn,
        ):
            await silent_client.turn_started.wait()
            await busy_client.turn_started.wait()
            for _ in range(3):
                await asyncio.sleep(HEARD_EVERY_S)
                busy_client.emit_turn(FIRST_TURN_ID, thinking())

            with pytest.raises(TurnResultAlreadyReported):
                await silent_turn
            assert failures(silent_tools) == silent_for(IDLE_S)
            assert not busy_turn.done()

            busy_client.emit_turn(FIRST_TURN_ID, *answered_turn())
            await busy_turn

        assert failures(busy_tools) == []
        assert busy_client.params_of(CodexRequestMethod.TURN_INTERRUPT) == []
        assert (
            silent_client.params_of(CodexRequestMethod.TURN_INTERRUPT)
            == INTERRUPTED_FIRST_TURN
        )

    @pytest.mark.parametrize(
        ("seconds", "refusal"),
        [
            pytest.param(0.0, "greater_than", id="zero"),
            pytest.param(-1.0, "greater_than", id="negative"),
            pytest.param(float("inf"), "finite_number", id="infinite"),
            pytest.param(float("nan"), "finite_number", id="not-a-number"),
        ],
    )
    async def test_an_idle_limit_that_cannot_bound_a_wait_is_refused(
        self, seconds: float, refusal: str
    ) -> None:
        with pytest.raises(ValidationError) as refused:
            CodexAdapterConfig(turn_idle_timeout_s=seconds)

        assert [(error["loc"], error["type"]) for error in refused.value.errors()] == [
            (("turn_idle_timeout_s",), refusal)
        ]

    @pytest.mark.parametrize(
        "idle_s",
        [CEILING_S, CEILING_S + 1],
        ids=["equal-to-the-total", "above-the-total"],
    )
    async def test_an_idle_limit_that_could_never_apply_is_refused(
        self, idle_s: float
    ) -> None:
        """The total limit would always end the turn first."""
        with pytest.raises(ValidationError, match="turn_timeout_s") as refused:
            silence_guarded(turn_idle_timeout_s=idle_s)

        assert [(error["loc"], error["type"]) for error in refused.value.errors()] == [
            ((), "value_error")
        ]

    async def test_the_total_limit_wins_when_both_limits_end_together(
        self, started_rooms: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """A wait that starts with exactly the idle limit left of the total ends
        as the total limit, with its text."""
        total_s = 300.0
        client = FakeCodexClient()
        adapter = await started_rooms(
            {ROOM_ID: client}, silence_guarded(turn_timeout_s=total_s)
        )
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools) as turn:
            await client.turn_started.wait()
            for _ in range(2):
                await asyncio.sleep((total_s - IDLE_S) / 2)
                client.emit_turn(FIRST_TURN_ID, thinking())
            await asyncio.sleep(IDLE_S - MARGIN_S)
            assert not turn.done()
            assert failures(tools) == []

            await asyncio.sleep(2 * MARGIN_S)
            assert failures(tools) == past_its_total(total_s)
            with pytest.raises(TurnResultAlreadyReported):
                await turn
