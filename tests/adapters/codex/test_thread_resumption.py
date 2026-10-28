"""Codex adapter: thread resumption."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from band.adapters.codex import CodexAdapter
from band.converters.codex import CODEX_RESUME_RECORD
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.codex import CodexJsonRpcError, CodexRequestMethod
from band.runtime.tools import BandTool
from tests.adapters.codex.kit import (
    PROCESS_EXITED,
    ROOM_ID,
    ToolSchemaFakeTools,
    agent_input,
    answered_turn,
)
from tests.adapters.codexturns import (
    FIRST_THREAD_ID,
    FakeCodexClient,
    tool_call_request,
    turn_completed,
)

TRANSCRIPT = [
    {"message_type": "text", "content": "earlier context", "sender_name": "Alice"}
]
REPLAYED = "[Alice]: earlier context"


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

        raw_history = [
            {
                "message_type": "task",
                "content": "historical mapping",
                "metadata": {
                    "codex_thread_id": "thr-history",
                    "codex_room_id": ROOM_ID,
                    "codex_created_at": datetime.now(UTC).isoformat(),
                },
            }
        ]
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
        turn_input = turn_start["input"]
        history_items = [
            item for item in turn_input if "[Conversation History]" in item["text"]
        ]
        assert len(history_items) == 1
        assert "[Alice]: refactor the auth module" in history_items[0]["text"]
        assert (
            "[CodexAgent]: Done — split into auth_handler.py and middleware.py"
            in history_items[0]["text"]
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
    ) -> None:
        """Narration naming the unseeded thread is no resume record, and the
        next turn starts a fresh thread and replays the transcript into it."""
        room = processes()
        adapter = await started_codex(*room, emit_turn_task_markers=True)
        tools_failed, tools_next = ToolSchemaFakeTools(), ToolSchemaFakeTools()

        with pytest.raises(failure):
            await adapter.on_event(
                agent_input("first turn", tools_failed, raw_history=TRANSCRIPT)
            )
        await adapter.on_event(
            agent_input("next turn", tools_next, is_session_bootstrap=False)
        )

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
        assert REPLAYED in str(reseeding["input"])
        assert CODEX_RESUME_RECORD.candidates(tools_next.events_sent) == {
            ROOM_ID: reseeding["threadId"]
        }
