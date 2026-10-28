"""Codex adapter: thread resumption."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from band.adapters.codex import CodexAdapter
from band.converters.codex import CODEX_RESUME_RECORD
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.codex import CodexJsonRpcError, CodexRequestMethod
from band.runtime.formatters import build_participants_message
from band.runtime.tools import BandTool
from tests.adapters.codex.kit import (
    PROCESS_EXITED,
    REPLAYED,
    ROOM_ID,
    TRANSCRIPT,
    ToolSchemaFakeTools,
    agent_input,
    answered_turn,
    recorded_thread,
    replayed_history,
    stored,
)
from tests.adapters.codexturns import (
    FIRST_THREAD_ID,
    FakeCodexClient,
    tool_call_request,
    turn_completed,
)


def replayed(turn: dict[str, Any]) -> list[str]:
    """The transcript lines a ``turn/start`` replayed, if any."""
    return [
        line
        for text in replayed_history(turn)
        for line in text.splitlines()
        if line.startswith("[Alice]")
    ]


def turn_inputs(*processes: FakeCodexClient) -> list[dict[str, Any]]:
    """Every ``turn/start`` the room's processes received, in order."""
    return [
        params
        for process in processes
        for params in process.params_of(CodexRequestMethod.TURN_START)
    ]


class TestThreadResumption:
    """A room resumes its Codex thread from Band history, or rebuilds context when it cannot."""

    async def test_on_event_uses_converter_history_to_resume_thread(
        self, started_codex: Callable[[FakeCodexClient], Awaitable[CodexAdapter]]
    ) -> None:
        tools = ToolSchemaFakeTools()
        fake_client = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(fake_client)

        raw_history = [recorded_thread("thr-history")]
        await adapter.on_event(
            agent_input(
                "continue work",
                tools,
                raw_history=raw_history,
                room_id=ROOM_ID,
                is_session_bootstrap=True,
            )
        )

        methods = fake_client.request_methods
        assert CodexRequestMethod.THREAD_RESUME in methods
        assert CodexRequestMethod.THREAD_START not in methods
        assert any("Status: resumed" in event["content"] for event in tools.events_sent)

    async def test_restart_rehydrates_mapping_from_previous_task_events(
        self, started_codex: Callable[[FakeCodexClient], Awaitable[CodexAdapter]]
    ) -> None:
        tools_first = ToolSchemaFakeTools()
        fake_client_first = FakeCodexClient(events=answered_turn())
        adapter_first = await started_codex(fake_client_first)
        await adapter_first.on_event(
            agent_input(
                "first turn",
                tools_first,
                room_id="room-9",
                is_session_bootstrap=True,
            )
        )

        persisted_history = list(tools_first.events_sent)

        tools_second = ToolSchemaFakeTools()
        fake_client_second = FakeCodexClient(events=answered_turn())
        adapter_second = await started_codex(fake_client_second)
        await adapter_second.on_event(
            agent_input(
                "second turn after restart",
                tools_second,
                raw_history=persisted_history,
                room_id="room-9",
                is_session_bootstrap=True,
            )
        )

        methods_second = fake_client_second.request_methods
        assert CodexRequestMethod.THREAD_RESUME in methods_second
        assert CodexRequestMethod.THREAD_START not in methods_second

    async def test_resume_failure_injects_conversation_history(
        self, started_codex: Callable[[FakeCodexClient], Awaitable[CodexAdapter]]
    ) -> None:
        """Full lifecycle: first session produces history, second session resume fails,
        verify turn input contains history context."""
        # --- First session: produce some history ---
        tools_first = ToolSchemaFakeTools()
        fake_client_first = FakeCodexClient(events=answered_turn())
        adapter_first = await started_codex(fake_client_first)
        await adapter_first.on_event(
            agent_input(
                "refactor the auth module",
                tools_first,
                room_id="room-hist",
                is_session_bootstrap=True,
            )
        )

        # Simulate persisted history: task events + text messages
        persisted_history = list(tools_first.events_sent)
        persisted_history.append(
            {
                "message_type": "text",
                "content": "refactor the auth module",
                "sender_name": "Alice",
            }
        )
        persisted_history.append(
            {
                "message_type": "text",
                "content": "Done — split into auth_handler.py and middleware.py",
                "sender_name": "CodexAgent",
            }
        )

        # --- Second session: resume fails, should inject history ---
        tools_second = ToolSchemaFakeTools()
        fake_client_second = FakeCodexClient(
            events=answered_turn(),
            resume_error=CodexJsonRpcError(code=-32002, message="Thread expired"),
        )
        adapter_second = await started_codex(fake_client_second)
        await adapter_second.on_event(
            agent_input(
                "now add rate limiting",
                tools_second,
                raw_history=persisted_history,
                room_id="room-hist",
                is_session_bootstrap=True,
            )
        )

        methods = fake_client_second.request_methods
        assert CodexRequestMethod.THREAD_RESUME in methods
        assert CodexRequestMethod.THREAD_START in methods

        turn_start = fake_client_second.params_of(CodexRequestMethod.TURN_START)[0]
        (history,) = replayed_history(turn_start)
        assert "[Alice]: refactor the auth module" in history
        assert (
            "[CodexAgent]: Done — split into auth_handler.py and middleware.py"
            in history
        )

    async def test_a_room_resumes_its_thread_with_task_events_disabled(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        silenced = {"emit": ()}
        tools_first = ToolSchemaFakeTools()
        adapter_first = await started_codex(
            FakeCodexClient(events=answered_turn()), features=silenced
        )
        await adapter_first.on_event(agent_input("first turn", tools_first))

        restarted = FakeCodexClient(events=answered_turn())
        adapter_second = await started_codex(restarted, features=silenced)
        await adapter_second.on_event(
            agent_input(
                "after restart",
                ToolSchemaFakeTools(),
                raw_history=list(tools_first.events_sent),
            )
        )

        (resume,) = restarted.params_of(CodexRequestMethod.THREAD_RESUME)
        assert resume["threadId"] == FIRST_THREAD_ID
        assert CodexRequestMethod.THREAD_START not in restarted.request_methods

    async def test_with_no_recorded_thread_the_fresh_thread_is_sent_the_transcript(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        client = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(client)

        await adapter.on_event(
            agent_input("hello", ToolSchemaFakeTools(), raw_history=TRANSCRIPT)
        )

        assert CodexRequestMethod.THREAD_RESUME not in client.request_methods
        (seeding,) = turn_inputs(client)
        assert REPLAYED in str(seeding["input"])

    @pytest.mark.parametrize(
        ("bootstrap_history", "expected_replay"),
        [
            pytest.param(TRANSCRIPT, [REPLAYED, "[Alice]: first turn"], id="room"),
            pytest.param([], ["[Alice]: first turn"], id="new-room"),
        ],
    )
    @pytest.mark.parametrize(
        ("processes", "failure"),
        [
            pytest.param(
                lambda: [
                    FakeCodexClient(
                        events=[
                            turn_completed("turn-1", status="failed"),
                            tool_call_request(1, BandTool.NO_REPLY),
                            turn_completed("turn-2"),
                        ]
                    )
                ],
                TurnResultAlreadyReported,
                id="turn-failed",
            ),
            pytest.param(
                lambda: [
                    FakeCodexClient(
                        events=answered_turn(),
                        turn_start_error=CodexJsonRpcError(
                            code=-32603, message="refused"
                        ),
                    )
                ],
                CodexJsonRpcError,
                id="turn-refused",
            ),
            pytest.param(
                lambda: [
                    FakeCodexClient(events=[PROCESS_EXITED]),
                    FakeCodexClient(events=answered_turn()),
                ],
                TurnResultAlreadyReported,
                id="process-lost",
            ),
        ],
    )
    async def test_a_failed_seed_is_never_recorded_and_the_next_turn_reseeds(
        self,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
        processes: Callable[[], list[FakeCodexClient]],
        failure: type[Exception],
        bootstrap_history: list[dict[str, Any]],
        expected_replay: list[str],
    ) -> None:
        """Narration naming the unseeded thread is no resume record, and the
        next turn starts a fresh thread and replays the room's stored
        transcript into it, the failed turn's request included, with the
        room's roster the abandoned thread took with it."""
        room = processes()
        adapter = await started_codex(*room, emit_turn_task_markers=True)
        tools_failed, tools_next = ToolSchemaFakeTools(), ToolSchemaFakeTools()
        failed = agent_input("first turn", tools_failed, raw_history=bootstrap_history)
        following = agent_input("next turn", tools_next, is_session_bootstrap=False)
        tools_next.set_room_context(
            [*bootstrap_history, stored(failed.msg), stored(following.msg)]
        )

        with pytest.raises(failure):
            await adapter.on_event(failed)
        await adapter.on_event(following)

        assert CODEX_RESUME_RECORD.candidates(tools_failed.events_sent) == {}
        assert all(
            CodexRequestMethod.THREAD_RESUME not in process.request_methods
            for process in room
        )
        starts = sum(
            p.request_methods.count(CodexRequestMethod.THREAD_START) for p in room
        )
        assert starts == 2
        reseeding = turn_inputs(*room)[-1]
        assert replayed(reseeding) == expected_replay
        roster = build_participants_message(tools_next.participants)
        assert any(roster in item["text"] for item in reseeding["input"])
        assert CODEX_RESUME_RECORD.candidates(tools_next.events_sent) == {
            ROOM_ID: reseeding["threadId"]
        }

    @pytest.mark.looptime
    async def test_a_timed_out_seed_is_never_recorded_and_the_next_turn_reseeds(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """A seeding turn past its deadline raises before it could count as
        seeded, so its thread is abandoned like any failed seed."""
        client = FakeCodexClient()
        adapter = await started_codex(client, turn_timeout_s=60)
        tools_failed, tools_next = ToolSchemaFakeTools(), ToolSchemaFakeTools()
        timed_out = agent_input("first turn", tools_failed, raw_history=TRANSCRIPT)
        following = agent_input("next turn", tools_next, is_session_bootstrap=False)
        tools_next.set_room_context(
            [*TRANSCRIPT, stored(timed_out.msg), stored(following.msg)]
        )

        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_event(timed_out)
        client.script(*answered_turn("turn-2"))
        await adapter.on_event(following)

        assert CODEX_RESUME_RECORD.candidates(tools_failed.events_sent) == {}
        assert client.request_methods.count(CodexRequestMethod.THREAD_START) == 2
        reseeding = turn_inputs(client)[-1]
        assert reseeding["threadId"] != FIRST_THREAD_ID
        assert replayed(reseeding) == [REPLAYED, "[Alice]: first turn"]
        assert CODEX_RESUME_RECORD.candidates(tools_next.events_sent) == {
            ROOM_ID: reseeding["threadId"]
        }

    async def test_an_interrupted_seed_is_recorded_and_its_thread_continues(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """Codex took the interrupted turn's input, transcript included, so
        the thread is seeded: the room keeps it and Band history names it."""
        client = FakeCodexClient(
            events=[
                turn_completed("turn-1", status="interrupted"),
                *answered_turn("turn-2"),
            ]
        )
        adapter = await started_codex(client)
        tools_first = ToolSchemaFakeTools()

        await adapter.on_event(
            agent_input("start a long job", tools_first, raw_history=TRANSCRIPT)
        )
        await adapter.on_event(
            agent_input("continue", ToolSchemaFakeTools(), is_session_bootstrap=False)
        )

        seeding, following = turn_inputs(client)
        assert following["threadId"] == seeding["threadId"] == FIRST_THREAD_ID
        assert replayed(following) == []
        assert CODEX_RESUME_RECORD.candidates(tools_first.events_sent) == {
            ROOM_ID: FIRST_THREAD_ID
        }

    async def test_a_thread_is_recorded_once_and_a_failed_post_is_retried(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        client = FakeCodexClient(
            events=[
                *answered_turn("turn-1"),
                *answered_turn("turn-2"),
                *answered_turn("turn-3"),
            ]
        )
        # Without narration the record is the turn's only event.
        adapter = await started_codex(client, features={"emit": ()})
        tools = ToolSchemaFakeTools()
        tools.send_event_error = ConnectionError("Band REST unavailable")

        await adapter.on_event(agent_input("first", tools, raw_history=TRANSCRIPT))
        tools.send_event_error = None
        for content in ("second", "third"):
            await adapter.on_event(
                agent_input(content, tools, is_session_bootstrap=False)
            )

        records = [
            event
            for event in tools.events_sent
            if event["content"] == CODEX_RESUME_RECORD.label
        ]
        assert len(records) == 1
        assert CODEX_RESUME_RECORD.candidates(records) == {ROOM_ID: FIRST_THREAD_ID}
