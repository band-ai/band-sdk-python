"""Compaction is visible before either Letta endpoint finishes its SSE response."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.adapters.letta import LettaAdapter, LettaRun
from band.core.types import TurnUsage
from band.testing import FakeAgentTools
from band.testing.activity import ActivityRecorder, SHOWN_MEMORY, SHOWN_REASONING
from tests.adapters.letta.compaction.peer import CompactionStream, RESULT


class TestStreamCompaction:
    @pytest.mark.parametrize(
        ("conversation", "expected_usage"),
        [
            (None, TurnUsage(input_tokens=11, output_tokens=7)),
            ("conversation", TurnUsage()),
        ],
        ids=["per-room", "shared"],
    )
    async def test_memory_precedes_completion_and_durable_output_keeps_its_value(
        self,
        streamed_client: tuple[Any, CompactionStream],
        conversation: str | None,
        expected_usage: TurnUsage,
    ) -> None:
        client, peer = streamed_client
        adapter = LettaAdapter(emit=())
        tools = FakeAgentTools()
        recorder = ActivityRecorder()
        recorder.observing(tools)
        messages = [{"role": "user", "content": "perform work"}]
        try:
            async with asyncio.TaskGroup() as group:
                running = group.create_task(
                    adapter._call_provider(
                        LettaRun(client, "agent", conversation), messages, tools
                    )
                )
                await peer.paused.wait()
                assert recorder.events == [SHOWN_MEMORY]
                assert not running.done()
                peer.release.set()
                response, usage = await running
        finally:
            peer.release.set()
        await peer.closed.wait()
        assert recorder.events == [SHOWN_MEMORY, SHOWN_REASONING]
        assert usage == expected_usage
        assert [message.content for message in response] == [RESULT]
        assert peer.requests == [
            {
                "messages": messages,
                "streaming": True,
                "stream_tokens": False,
                "include_compaction_messages": True,
            }
        ]
