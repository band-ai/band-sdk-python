"""Tests for the A2A history converter and its round trip with the adapter."""

from __future__ import annotations

import pytest
from a2a.types import Task, TaskState, TaskStatus

from band.converters.a2a import A2AHistoryConverter
from band.integrations.a2a import A2AAdapter
from band.integrations.a2a.protocol import TERMINAL_TASK_STATE_NAMES
from band.testing import FakeAgentTools


class TestA2AHistoryConverter:
    def test_empty_history_yields_empty_state(self) -> None:
        state = A2AHistoryConverter().convert([])

        assert (state.context_id, state.task_id, state.task_state) == (
            None,
            None,
            None,
        )

    def test_latest_a2a_task_event_wins(self) -> None:
        history = [
            {
                "message_type": "task",
                "metadata": {
                    "a2a_context_id": "ctx-old",
                    "a2a_task_id": "task-old",
                    "a2a_task_state": "TASK_STATE_COMPLETED",
                },
            },
            {"message_type": "text", "content": "New message"},
            {
                "message_type": "task",
                "metadata": {
                    "a2a_context_id": "ctx-new",
                    "a2a_task_id": "task-new",
                    "a2a_task_state": "TASK_STATE_INPUT_REQUIRED",
                },
            },
        ]

        state = A2AHistoryConverter().convert(history)

        assert (state.context_id, state.task_id) == ("ctx-new", "task-new"), (
            "rehydration must resume from the most recent task event"
        )
        assert state.task_state == "TASK_STATE_INPUT_REQUIRED"

    def test_non_a2a_task_events_are_ignored(self) -> None:
        history = [{"message_type": "task", "metadata": {"other_key": "value"}}]

        state = A2AHistoryConverter().convert(history)

        assert state.context_id is None, (
            "task events from other adapters must not pollute A2A session state"
        )

    @pytest.mark.asyncio
    async def test_round_trips_emitted_task_events_into_terminal_state(self) -> None:
        """The write side (adapter task events), the read side (converter) and
        the terminal-state vocabulary must agree, or rooms rejoin amnesiac or
        resubscribe to finished tasks."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        tools = FakeAgentTools()
        task = Task(
            id="task-123",
            context_id="ctx-123",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )

        await adapter._emit_task_event(tools, task, task.status.state)
        history = [
            {
                "message_type": event["message_type"],
                "content": event["content"],
                "metadata": event["metadata"],
            }
            for event in tools.events_sent
        ]
        state = A2AHistoryConverter().convert(history)

        assert (state.context_id, state.task_id) == ("ctx-123", "task-123"), (
            "the converter must read back exactly what the adapter persisted"
        )
        assert state.task_state in TERMINAL_TASK_STATE_NAMES, (
            "a completed task must rehydrate as terminal, or bootstrap "
            "resubscribes to a finished task"
        )
