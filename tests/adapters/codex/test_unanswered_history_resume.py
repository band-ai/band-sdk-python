"""Codex adapter: a thread Band history names whose resume goes unanswered is retried once, then replaced."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import pytest

from band.adapters.codex import PROCESS_NOT_READY_ERROR, CodexAdapter, CodexCommand
from band.adapters.codex.commands import CodexSubcommand
from band.client.streaming import ControlMode
from band.converters.codex import CodexHistoryConverter
from band.integrations.codex import CodexJsonRpcError
from band.integrations.codex.types import CodexRequestMethod, CodexSessionState
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.codex.kit import (
    REPLAYED,
    ROOM_ID,
    TRANSCRIPT,
    ToolSchemaFakeTools,
    UnansweredRequestClient,
    agent_input,
    answered_turn,
    recorded_thread,
    replayed_history,
    send_bootstrap,
)
from tests.adapters.codexturns import FIRST_THREAD_ID, FakeCodexClient
from tests.adapters.dispatch import deliver_event
from tests.toolkit.tasks import background

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


def silent_resume() -> UnansweredRequestClient:
    """A process that takes ``thread/resume`` and never answers it."""
    return UnansweredRequestClient(
        events=[], held=CodexRequestMethod.THREAD_RESUME, settle_on_close=True
    )


def resumed_threads(client: FakeCodexClient) -> list[str]:
    """The threads ``client`` was asked to resume, in order."""
    return [
        resumed["threadId"]
        for resumed in client.params_of(CodexRequestMethod.THREAD_RESUME)
    ]


def held_resume(client: UnansweredRequestClient) -> str | None:
    """The thread ``client`` was asked to resume and never answered for."""
    params = client.unanswered_params
    return None if params is None else params["threadId"]


def turn_threads(client: FakeCodexClient) -> list[str]:
    """The threads ``client`` ran turns on, in order."""
    return [
        started["threadId"]
        for started in client.params_of(CodexRequestMethod.TURN_START)
    ]


def failure_messages(tools: FakeAgentTools) -> list[str]:
    """The messages of every failure a delivery reported."""
    return [failure["message"] for failure in reported_failures(tools)]


async def send_later(
    adapter: CodexAdapter, content: str, tools: FakeAgentTools
) -> None:
    """Deliver a message that is not the room's first."""
    await deliver_event(
        adapter, agent_input(content, tools, is_session_bootstrap=False)
    )


@pytest.mark.looptime
class TestUnansweredHistoryResume:
    async def test_the_next_turn_retries_the_thread_on_a_new_process(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """The failed turn was the room's first, so its next message is an
        ordinary one. It still continues the thread Band history records."""
        replacement = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(silent_resume(), replacement)
        unheard, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()
        with pytest.raises(TimeoutError):
            await send_bootstrap(adapter, tools=unheard, history=RECORDED)
        await send_later(adapter, "and now?", tools)

        assert failure_messages(unheard) == [PROCESS_NOT_READY_ERROR]
        assert resumed_threads(replacement) == [RECORDED_THREAD]
        assert replacement.params_of(CodexRequestMethod.THREAD_START) == []
        assert turn_threads(replacement) == [RECORDED_THREAD]
        assert failure_messages(tools) == []

    async def test_a_newer_history_record_supersedes_the_pending_retry(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        newer_thread = "thr-newer"
        latest = CodexSessionState(room_to_thread={ROOM_ID: newer_thread})
        replacement = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(silent_resume(), replacement)

        with pytest.raises(TimeoutError):
            await send_bootstrap(adapter, history=RECORDED)
        await send_bootstrap(adapter, history=latest)

        assert resumed_threads(replacement) == [newer_thread]
        assert turn_threads(replacement) == [newer_thread]

    async def test_a_refused_retry_starts_a_new_thread_from_the_room_history(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """The retry has the fallback a room's first turn has: refused, it
        starts a new thread in the same turn and seeds it with the history."""
        replacement = FakeCodexClient(
            events=answered_turn(),
            resume_error=CodexJsonRpcError(code=-32002, message="Not found"),
        )
        adapter = await started_codex(silent_resume(), replacement)
        tools = ToolSchemaFakeTools()
        tools.set_room_context(TRANSCRIPT)
        with pytest.raises(TimeoutError):
            await send_bootstrap(adapter, history=RECORDED)
        await send_later(adapter, "and now?", tools)

        assert resumed_threads(replacement) == [RECORDED_THREAD]
        (started,) = replacement.params_of(CodexRequestMethod.TURN_START)
        assert started["threadId"] == FIRST_THREAD_ID
        (replay,) = replayed_history(started)
        assert REPLAYED in replay
        assert failure_messages(tools) == []

    async def test_a_retry_that_goes_unanswered_too_gives_the_thread_up(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """A second silence fails one more turn. The room then gives the thread
        up: its next turn starts a new one on a new process."""
        retry = silent_resume()
        fresh = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(silent_resume(), retry, fresh)
        again, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()
        tools.set_room_context(TRANSCRIPT)
        with pytest.raises(TimeoutError):
            await send_bootstrap(adapter, history=RECORDED)
        with pytest.raises(TimeoutError):
            await send_later(adapter, "again?", again)
        await send_later(adapter, "and now?", tools)

        assert failure_messages(again) == [PROCESS_NOT_READY_ERROR]
        assert held_resume(retry) == RECORDED_THREAD
        assert retry.closed
        assert resumed_threads(fresh) == []
        (started,) = fresh.params_of(CodexRequestMethod.TURN_START)
        assert started["threadId"] == FIRST_THREAD_ID
        (replay,) = replayed_history(started)
        assert REPLAYED in replay
        assert failure_messages(tools) == []

    async def test_after_an_answered_retry_the_thread_is_the_rooms(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """The message after an answered retry runs on the recorded thread
        without resuming it again."""
        replacement = FakeCodexClient(
            events=[*answered_turn(), *answered_turn("turn-2")]
        )
        adapter = await started_codex(silent_resume(), replacement)
        tools = ToolSchemaFakeTools()
        with pytest.raises(TimeoutError):
            await send_bootstrap(adapter, history=RECORDED)
        await send_later(adapter, "and now?", ToolSchemaFakeTools())
        await send_later(adapter, "and then?", tools)

        assert resumed_threads(replacement) == [RECORDED_THREAD]
        assert turn_threads(replacement) == [RECORDED_THREAD, RECORDED_THREAD]
        assert failure_messages(tools) == []

    async def test_the_archive_command_drops_a_pending_retry(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """Archived after the failed first turn, the thread is not tried again:
        the next message starts a new thread on the replacement process."""
        replacement = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(silent_resume(), replacement)
        tools = ToolSchemaFakeTools()
        with pytest.raises(TimeoutError):
            await send_bootstrap(adapter, history=RECORDED)
        await send_later(adapter, ARCHIVE, ToolSchemaFakeTools())
        await send_bootstrap(adapter, content="and now?", tools=tools, history=RECORDED)

        assert resumed_threads(replacement) == []
        assert turn_threads(replacement) == [FIRST_THREAD_ID]
        assert failure_messages(tools) == []

    async def test_a_stop_during_the_retry_spends_it(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """The stop retires the process the retry waited on, and the room's
        next message starts a new thread rather than trying the thread again."""
        retry = silent_resume()
        fresh = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(silent_resume(), retry, fresh)
        stopped, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()
        with pytest.raises(TimeoutError):
            await send_bootstrap(adapter, history=RECORDED)
        async with background(send_later(adapter, "again?", stopped)) as retried:
            await retry.unanswered.arrived()
            await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
            with pytest.raises(asyncio.CancelledError):
                await retried
        await send_bootstrap(adapter, content="and now?", tools=tools, history=RECORDED)

        assert retry.closed
        assert held_resume(retry) == RECORDED_THREAD
        assert resumed_threads(fresh) == []
        assert turn_threads(fresh) == [FIRST_THREAD_ID]
        assert failure_messages(stopped) == failure_messages(tools) == []

    async def test_a_given_up_thread_stays_given_up_when_history_names_it_again(
        self,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Band history names the thread until a turn completes, and some hosts
        hand it to the room with every message. The room does not try the
        thread a third time: the message starts a new thread from that history."""
        fresh = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(silent_resume(), silent_resume(), fresh)
        tools = ToolSchemaFakeTools()
        with caplog.at_level(logging.WARNING, logger="band.adapters.codex"):
            with pytest.raises(TimeoutError):
                await send_bootstrap(adapter, history=RECORDED_WITH_TRANSCRIPT)
            with pytest.raises(TimeoutError):
                await send_bootstrap(adapter, history=RECORDED_WITH_TRANSCRIPT)
            await send_bootstrap(adapter, tools=tools, history=RECORDED_WITH_TRANSCRIPT)

        assert resumed_threads(fresh) == []
        (started,) = fresh.params_of(CodexRequestMethod.TURN_START)
        assert started["threadId"] == FIRST_THREAD_ID
        (replay,) = replayed_history(started)
        assert REPLAYED in replay
        assert failure_messages(tools) == []
        assert caplog.messages.count(GIVEN_UP) == 1

    async def test_the_archive_command_does_not_bring_a_given_up_thread_back(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """Archiving asks for a new thread, so it must not let the history
        that still names the given-up thread bring that thread back."""
        fresh = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(silent_resume(), silent_resume(), fresh)
        tools = ToolSchemaFakeTools()
        with pytest.raises(TimeoutError):
            await send_bootstrap(adapter, history=RECORDED_WITH_TRANSCRIPT)
        with pytest.raises(TimeoutError):
            await send_bootstrap(adapter, history=RECORDED_WITH_TRANSCRIPT)
        await send_bootstrap(adapter, content=ARCHIVE, history=RECORDED_WITH_TRANSCRIPT)
        await send_bootstrap(adapter, tools=tools, history=RECORDED_WITH_TRANSCRIPT)

        assert resumed_threads(fresh) == []
        assert turn_threads(fresh) == [FIRST_THREAD_ID]
        assert failure_messages(tools) == []

    async def test_archiving_an_unrecorded_replacement_keeps_the_old_resume_spent(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        fresh = FakeCodexClient(events=[*answered_turn(), *answered_turn("turn-2")])
        adapter = await started_codex(
            silent_resume(), silent_resume(), fresh, features={"emit": ()}
        )
        with pytest.raises(TimeoutError):
            await send_bootstrap(adapter, history=RECORDED)
        with pytest.raises(TimeoutError):
            await send_bootstrap(adapter, history=RECORDED)

        lost_record = ToolSchemaFakeTools()
        lost_record.send_event_error = ConnectionError("resume record unavailable")
        await send_bootstrap(adapter, tools=lost_record, history=RECORDED)
        await send_bootstrap(adapter, content=ARCHIVE, history=RECORDED)
        await send_bootstrap(adapter, history=RECORDED)

        assert resumed_threads(fresh) == []
        first, second = turn_threads(fresh)
        assert first != second
