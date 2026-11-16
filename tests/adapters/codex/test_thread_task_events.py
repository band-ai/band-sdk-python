"""Codex adapter: the task event naming the room's Codex thread."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from band.adapters.codex import CodexAdapter
from band.adapters.codex.narration import task_event_content
from band.core.types import MessageType
from band.integrations.codex.types import CodexMetadataKey
from band.testing import events_of_type
from tests.adapters.codex.kit import (
    ROOM_ID,
    ToolSchemaFakeTools,
    agent_input,
    answered_turn,
    recorded_thread,
)
from tests.adapters.codexturns import FIRST_THREAD_ID, FakeCodexClient


def thread_events(tools: ToolSchemaFakeTools) -> list[tuple[str, dict[str, Any]]]:
    """Each Codex thread task event's content and metadata, in order."""
    return [
        (event["content"], event["metadata"])
        for event in events_of_type(tools, MessageType.TASK)
        if CodexMetadataKey.THREAD in event["metadata"]
        and "Task: Codex thread" in event["content"]
    ]


class TestThreadTaskEvents:
    """A room reports a fresh thread as mapped and a recorded one as resumed."""

    @pytest.mark.asyncio
    async def test_a_fresh_thread_is_reported_mapped_with_its_transport(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        adapter = await started_codex(FakeCodexClient(events=answered_turn()))
        tools = ToolSchemaFakeTools()

        await adapter.on_event(
            agent_input("hello", tools, room_id=ROOM_ID, is_session_bootstrap=True)
        )

        [(content, metadata)] = thread_events(tools)
        assert content == task_event_content(
            task_id=FIRST_THREAD_ID,
            task="Codex thread",
            status="mapped",
            summary="Transport: stdio",
        )
        assert metadata[CodexMetadataKey.THREAD] == FIRST_THREAD_ID
        assert metadata[CodexMetadataKey.ROOM] == ROOM_ID
        assert metadata[CodexMetadataKey.TRANSPORT] == "stdio"
        assert "codex_created_at" in metadata

    @pytest.mark.asyncio
    async def test_a_recorded_thread_is_reported_resumed(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        adapter = await started_codex(FakeCodexClient(events=answered_turn()))
        tools = ToolSchemaFakeTools()

        await adapter.on_event(
            agent_input(
                "continue",
                tools,
                raw_history=[recorded_thread("thr-history")],
                room_id=ROOM_ID,
                is_session_bootstrap=True,
            )
        )

        assert thread_events(tools) == [
            (
                task_event_content(
                    task_id="thr-history",
                    task="Codex thread",
                    status="resumed",
                    summary=f"Room: {ROOM_ID}",
                ),
                {
                    CodexMetadataKey.THREAD: "thr-history",
                    CodexMetadataKey.ROOM: ROOM_ID,
                    "codex_resumed": True,
                },
            )
        ]
