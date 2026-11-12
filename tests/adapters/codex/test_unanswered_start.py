"""Codex adapter: a process that does not answer a request its turn waits on frees its room."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import pytest

from band.adapters.codex import (
    CODEX_TRANSITION_TIMEOUT_S,
    CodexAdapter,
    CodexCommand,
    CodexSubcommand,
)
from band.client.streaming import ControlMode
from band.converters.codex import CodexHistoryConverter
from band.core.protocols import FAILURE_CODE_TIMEOUT, TurnResultAlreadyReported
from band.core.retention import RoomRetentionConfig
from band.integrations.codex import CodexJsonRpcError
from band.integrations.codex.types import CodexRequestMethod, CodexSessionState
from tests.adapters.codex.kit import (
    REPLAYED,
    ROOM_ID,
    TRANSCRIPT,
    ToolSchemaFakeTools,
    UnansweringClient,
    agent_input,
    answered_turn,
    recorded_thread,
    replayed_history,
    send_bootstrap,
)
from tests.adapters.codex.timedturn import MARGIN_S, failures, running_turn
from tests.adapters.codexturns import (
    FIRST_THREAD_ID,
    FakeCodexClient,
)
from tests.adapters.dispatch import deliver_event
from tests.adapters.waiting import until

# A thread Band history records for the room, which its first turn resumes.
RECORDED_THREAD = "thr-recorded"
RECORDED = CodexSessionState(room_to_thread={ROOM_ID: RECORDED_THREAD})
# The history a host that delivers every message as a session bootstrap hands
# the room each time: its transcript, and the record naming its thread, which
# stays the newest until a turn completes.
RECORDED_WITH_TRANSCRIPT = CodexHistoryConverter().convert(
    [*TRANSCRIPT, recorded_thread(RECORDED_THREAD)]
)
ARCHIVE = f"/{CodexCommand.THREAD} {CodexSubcommand.ARCHIVE}"
GIVEN_UP = (
    f"Room {ROOM_ID}: giving up Codex thread {RECORDED_THREAD}; "
    "its resume went unanswered twice"
)
# Each request a room's first turn waits on before it runs, the history that
# makes the turn send it, and the name its failure gives it.
BEFORE_A_TURN_RUNS = pytest.mark.parametrize(
    ("request_method", "history", "named"),
    [
        pytest.param(CodexRequestMethod.THREAD_START, None, "thread_start"),
        pytest.param(CodexRequestMethod.THREAD_RESUME, RECORDED, "thread_resume"),
        pytest.param(CodexRequestMethod.TURN_START, None, "turn_start"),
    ],
    ids=["thread-start", "thread-resume", "turn-start"],
)


def unanswered(
    named: str, bound_s: float = CODEX_TRANSITION_TIMEOUT_S
) -> list[tuple[str, str]]:
    """The one failure of a turn whose ``named`` request Codex never answered."""
    text = f"Codex turn timed out after {bound_s}s of silence ({named})"
    return [(FAILURE_CODE_TIMEOUT, text)]


def resumed_threads(client: FakeCodexClient) -> list[str]:
    """The threads ``client`` was asked to resume, in order."""
    return [
        resumed["threadId"]
        for resumed in client.params_of(CodexRequestMethod.THREAD_RESUME)
    ]


@pytest.mark.looptime
class TestUnansweredStart:
    @BEFORE_A_TURN_RUNS
    async def test_an_unanswered_request_is_reported_once_as_a_timeout(
        self,
        request_method: CodexRequestMethod,
        history: CodexSessionState | None,
        named: str,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
    ) -> None:
        """Codex gets as long as a process gets to start; no turn limit runs yet."""
        client = UnansweringClient(request_method)
        adapter = await started_codex(client)
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools, history=history) as turn:
            await client.asked.wait()
            await asyncio.sleep(CODEX_TRANSITION_TIMEOUT_S - MARGIN_S)
            assert not turn.done()
            assert failures(tools) == []

            await asyncio.sleep(2 * MARGIN_S)
            assert failures(tools) == unanswered(named)
            with pytest.raises(TurnResultAlreadyReported):
                await turn

        assert failures(tools) == unanswered(named)

    @BEFORE_A_TURN_RUNS
    async def test_the_process_that_did_not_answer_is_replaced(
        self,
        request_method: CodexRequestMethod,
        history: CodexSessionState | None,
        named: str,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
    ) -> None:
        """The old process is closed and a new one answers the next message."""
        client = UnansweringClient(request_method)
        replacement = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(client, replacement)
        unheard, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()

        async with running_turn(adapter, unheard, history=history) as turn:
            await client.asked.wait()
            await asyncio.sleep(CODEX_TRANSITION_TIMEOUT_S + MARGIN_S)
            assert failures(unheard) == unanswered(named)
            with pytest.raises(TurnResultAlreadyReported):
                await turn
        await send_bootstrap(adapter, tools=tools, history=history)

        assert client.closed
        assert len(replacement.params_of(CodexRequestMethod.TURN_START)) == 1
        assert failures(tools) == []

    async def test_a_recorded_thread_survives_an_unanswered_history_resume(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """The failed turn was the room's first, so its next message is an
        ordinary one. It still continues the thread Band history records."""
        client = UnansweringClient(CodexRequestMethod.THREAD_RESUME)
        replacement = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(client, replacement)
        unheard, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()

        async with running_turn(adapter, unheard, history=RECORDED) as turn:
            await client.asked.wait()
            await asyncio.sleep(CODEX_TRANSITION_TIMEOUT_S + MARGIN_S)
            assert failures(unheard) == unanswered("thread_resume")
            with pytest.raises(TurnResultAlreadyReported):
                await turn
        await deliver_event(
            adapter, agent_input("and now?", tools, is_session_bootstrap=False)
        )

        assert resumed_threads(replacement) == [RECORDED_THREAD]
        assert replacement.params_of(CodexRequestMethod.THREAD_START) == []
        assert [
            started["threadId"]
            for started in replacement.params_of(CodexRequestMethod.TURN_START)
        ] == [RECORDED_THREAD]
        assert failures(tools) == []

    async def test_a_recorded_thread_the_next_process_refuses_gives_way(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """The next turn tries the thread once more, with the fallback a room's
        first turn has: refused, it starts a new thread in the same turn and
        seeds it with the room's history."""
        client = UnansweringClient(CodexRequestMethod.THREAD_RESUME)
        replacement = FakeCodexClient(
            events=answered_turn(),
            resume_error=CodexJsonRpcError(code=-32002, message="Not found"),
        )
        adapter = await started_codex(client, replacement)
        unheard, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()
        tools.set_room_context(TRANSCRIPT)
        with pytest.raises(TurnResultAlreadyReported):
            await send_bootstrap(adapter, tools=unheard, history=RECORDED)
        await deliver_event(
            adapter, agent_input("and now?", tools, is_session_bootstrap=False)
        )

        assert failures(unheard) == unanswered("thread_resume")
        assert resumed_threads(replacement) == [RECORDED_THREAD]
        (started,) = replacement.params_of(CodexRequestMethod.TURN_START)
        assert started["threadId"] == FIRST_THREAD_ID
        (replay,) = replayed_history(started)
        assert REPLAYED in replay
        assert failures(tools) == []

    async def test_a_recorded_thread_unanswered_twice_is_given_up(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """A second silence fails one more turn. The room then gives the thread
        up: its next turn starts a new one on a new process."""
        client = UnansweringClient(CodexRequestMethod.THREAD_RESUME)
        retry = UnansweringClient(CodexRequestMethod.THREAD_RESUME)
        fresh = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(client, retry, fresh)
        unheard, again, tools = (
            ToolSchemaFakeTools(),
            ToolSchemaFakeTools(),
            ToolSchemaFakeTools(),
        )
        tools.set_room_context(TRANSCRIPT)
        with pytest.raises(TurnResultAlreadyReported):
            await send_bootstrap(adapter, tools=unheard, history=RECORDED)
        with pytest.raises(TurnResultAlreadyReported):
            await deliver_event(
                adapter, agent_input("again?", again, is_session_bootstrap=False)
            )
        await deliver_event(
            adapter, agent_input("and now?", tools, is_session_bootstrap=False)
        )

        assert failures(unheard) == failures(again) == unanswered("thread_resume")
        assert resumed_threads(retry) == [RECORDED_THREAD]
        assert retry.closed
        assert resumed_threads(fresh) == []
        (started,) = fresh.params_of(CodexRequestMethod.TURN_START)
        assert started["threadId"] == FIRST_THREAD_ID
        (replay,) = replayed_history(started)
        assert REPLAYED in replay
        assert failures(tools) == []

    async def test_the_archive_command_drops_a_pending_retry(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """Archived after the failed first turn, the thread is not tried again:
        the next message starts a new thread on the replacement process."""
        client = UnansweringClient(CodexRequestMethod.THREAD_RESUME)
        replacement = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(client, replacement)
        tools = ToolSchemaFakeTools()
        with pytest.raises(TurnResultAlreadyReported):
            await send_bootstrap(adapter, history=RECORDED)
        await deliver_event(
            adapter,
            agent_input(ARCHIVE, ToolSchemaFakeTools(), is_session_bootstrap=False),
        )
        await deliver_event(
            adapter, agent_input("and now?", tools, is_session_bootstrap=False)
        )

        assert resumed_threads(replacement) == []
        (started,) = replacement.params_of(CodexRequestMethod.TURN_START)
        assert started["threadId"] == FIRST_THREAD_ID
        assert failures(tools) == []

    async def test_a_stop_during_the_retried_resume_spends_the_retry(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """The stop closes the process the retry waited on, and the room's next
        message starts a new thread rather than trying the thread again."""
        client = UnansweringClient(CodexRequestMethod.THREAD_RESUME)
        retry = UnansweringClient(CodexRequestMethod.THREAD_RESUME)
        fresh = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(client, retry, fresh)
        stopped, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()
        with pytest.raises(TurnResultAlreadyReported):
            await send_bootstrap(adapter, history=RECORDED)
        retried = asyncio.create_task(
            deliver_event(
                adapter, agent_input("again?", stopped, is_session_bootstrap=False)
            )
        )
        try:
            await retry.asked.wait()
            await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
        finally:
            await asyncio.gather(retried, return_exceptions=True)
        await deliver_event(
            adapter, agent_input("and now?", tools, is_session_bootstrap=False)
        )

        assert retry.closed
        assert resumed_threads(retry) == [RECORDED_THREAD]
        assert resumed_threads(fresh) == []
        (started,) = fresh.params_of(CodexRequestMethod.TURN_START)
        assert started["threadId"] == FIRST_THREAD_ID
        assert failures(stopped) == failures(tools) == []

    async def test_after_an_answered_retry_the_thread_is_the_rooms(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """The message after an answered retry runs on the recorded thread
        without resuming it from history again."""
        client = UnansweringClient(CodexRequestMethod.THREAD_RESUME)
        replacement = FakeCodexClient(
            events=[*answered_turn(), *answered_turn("turn-2")]
        )
        adapter = await started_codex(client, replacement)
        tools = ToolSchemaFakeTools()
        with pytest.raises(TurnResultAlreadyReported):
            await send_bootstrap(adapter, history=RECORDED)
        await deliver_event(
            adapter,
            agent_input("and now?", ToolSchemaFakeTools(), is_session_bootstrap=False),
        )
        await deliver_event(
            adapter, agent_input("and then?", tools, is_session_bootstrap=False)
        )

        assert resumed_threads(replacement) == [RECORDED_THREAD]
        assert [
            started["threadId"]
            for started in replacement.params_of(CodexRequestMethod.TURN_START)
        ] == [RECORDED_THREAD, RECORDED_THREAD]
        assert failures(tools) == []

    async def test_a_given_up_thread_stays_given_up_when_history_names_it_again(
        self,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Band history names the thread until a turn completes, and some hosts
        hand it to the room with every message. The room does not try the
        thread a third time: the message starts a new thread from that history."""
        client = UnansweringClient(CodexRequestMethod.THREAD_RESUME)
        retry = UnansweringClient(CodexRequestMethod.THREAD_RESUME)
        fresh = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(client, retry, fresh)
        unheard, again, tools = (
            ToolSchemaFakeTools(),
            ToolSchemaFakeTools(),
            ToolSchemaFakeTools(),
        )
        with caplog.at_level(logging.WARNING, logger="band.adapters.codex"):
            with pytest.raises(TurnResultAlreadyReported):
                await send_bootstrap(
                    adapter, tools=unheard, history=RECORDED_WITH_TRANSCRIPT
                )
            with pytest.raises(TurnResultAlreadyReported):
                await send_bootstrap(
                    adapter, tools=again, history=RECORDED_WITH_TRANSCRIPT
                )
            await send_bootstrap(adapter, tools=tools, history=RECORDED_WITH_TRANSCRIPT)

        assert failures(unheard) == failures(again) == unanswered("thread_resume")
        assert resumed_threads(fresh) == []
        (started,) = fresh.params_of(CodexRequestMethod.TURN_START)
        assert started["threadId"] == FIRST_THREAD_ID
        (replay,) = replayed_history(started)
        assert REPLAYED in replay
        assert failures(tools) == []
        assert caplog.messages.count(GIVEN_UP) == 1

    async def test_the_archive_command_does_not_bring_a_given_up_thread_back(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """Archiving asks for a new thread, so it must not let the history
        that still names the given-up thread bring that thread back."""
        client = UnansweringClient(CodexRequestMethod.THREAD_RESUME)
        retry = UnansweringClient(CodexRequestMethod.THREAD_RESUME)
        fresh = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(client, retry, fresh)
        tools = ToolSchemaFakeTools()
        with pytest.raises(TurnResultAlreadyReported):
            await send_bootstrap(adapter, history=RECORDED_WITH_TRANSCRIPT)
        with pytest.raises(TurnResultAlreadyReported):
            await send_bootstrap(adapter, history=RECORDED_WITH_TRANSCRIPT)
        await send_bootstrap(adapter, content=ARCHIVE, history=RECORDED_WITH_TRANSCRIPT)
        await send_bootstrap(adapter, tools=tools, history=RECORDED_WITH_TRANSCRIPT)

        assert resumed_threads(fresh) == []
        (started,) = fresh.params_of(CodexRequestMethod.TURN_START)
        assert started["threadId"] == FIRST_THREAD_ID
        assert failures(tools) == []

    async def test_a_stop_during_an_unanswered_thread_start_replaces_the_process(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """A stop before the bound must not leave the silent process for the
        room's next message to wait on again."""
        client = UnansweringClient(CodexRequestMethod.THREAD_START)
        replacement = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(client, replacement)
        stopped, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()

        async with running_turn(adapter, stopped):
            await client.asked.wait()
            await asyncio.sleep(CODEX_TRANSITION_TIMEOUT_S / 2)
            await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
        await send_bootstrap(adapter, tools=tools)

        assert client.closed
        assert len(client.params_of(CodexRequestMethod.THREAD_START)) == 1
        assert len(replacement.params_of(CodexRequestMethod.TURN_START)) == 1
        assert failures(stopped) == []
        assert failures(tools) == []

    async def test_the_wait_is_as_long_as_the_rooms_own_transition_bound(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """An operator who changes how long a process gets to start changes
        this wait with it, and the failure names that bound."""
        bound_s = 5.0
        client = UnansweringClient(CodexRequestMethod.TURN_START)
        adapter = await started_codex(
            client, retention=RoomRetentionConfig(transition_timeout_s=bound_s)
        )
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools) as turn:
            await client.asked.wait()
            await asyncio.sleep(bound_s - MARGIN_S)
            assert not turn.done()
            assert failures(tools) == []

            await asyncio.sleep(2 * MARGIN_S)
            assert failures(tools) == unanswered("turn_start", bound_s)
            with pytest.raises(TurnResultAlreadyReported):
                await turn

    async def test_a_seeded_thread_continues_after_an_unanswered_turn_start(
        self,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
    ) -> None:
        client = UnansweringClient(
            CodexRequestMethod.TURN_START, answered=1, events=answered_turn()
        )
        replacement = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(client, replacement)
        unheard, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()
        await send_bootstrap(adapter, tools=ToolSchemaFakeTools())

        async with running_turn(adapter, unheard) as turn:
            await client.asked.wait()
            await asyncio.sleep(CODEX_TRANSITION_TIMEOUT_S + MARGIN_S)
            assert failures(unheard) == unanswered("turn_start")
            with pytest.raises(TurnResultAlreadyReported):
                await turn
        await send_bootstrap(adapter, tools=tools)

        assert client.closed
        assert resumed_threads(replacement) == [FIRST_THREAD_ID]
        assert failures(tools) == []

    async def test_an_answer_that_comes_within_the_bound_keeps_the_process(
        self,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
    ) -> None:
        client, spare = FakeCodexClient(events=answered_turn()), FakeCodexClient()
        client.turn_start_released.clear()
        adapter = await started_codex(client, spare)
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools) as turn:
            await until(
                lambda: bool(client.params_of(CodexRequestMethod.TURN_START)),
                failure="The adapter never sent turn/start",
            )
            await asyncio.sleep(CODEX_TRANSITION_TIMEOUT_S - MARGIN_S)
            assert failures(tools) == []
            client.turn_start_released.set()
            await turn

        assert failures(tools) == []
        assert not client.closed
        assert spare.requests == []
