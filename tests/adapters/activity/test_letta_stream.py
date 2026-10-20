from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from letta_client import AsyncLetta

from band.adapters.letta import LettaAdapter, LettaAdapterConfig, LettaRun
from band.testing import FakeAgentTools
from band.testing.activity import ActivityRecorder
from tests.adapters.activity.kit import LettaActivityStream


class TestLettaStreamActivity:
    @pytest.mark.parametrize("cancelled", [False, True])
    async def test_real_sdk_observes_stream_before_response_buffering_and_closes(
        self, cancelled: bool
    ) -> None:
        stream = LettaActivityStream()
        requests: list[dict[str, Any]] = []

        async def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=stream
            )

        recorder = ActivityRecorder()
        tools = FakeAgentTools()
        tools._activity_observer = recorder
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport,
            AsyncLetta(
                api_key="test", base_url="https://letta.test", http_client=transport
            ) as client,
        ):
            adapter = LettaAdapter(LettaAdapterConfig(agent_id="agent"), emit=())
            adapter._client = client
            task = asyncio.create_task(
                adapter._call_provider(
                    LettaRun(client, "agent", None),
                    [{"role": "user", "content": "hello"}],
                    tools,
                )
            )
            try:
                async with asyncio.timeout(5):
                    await stream.entered.wait()
                assert recorder.events == [{"kind": "tool_call", "name": "shell"}]
                assert not task.done()
                if cancelled:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    stream.release.set()
                    messages, usage = await task
                    assert [message.message_type for message in messages] == [
                        "tool_call_message",
                        "tool_return_message",
                    ]
                    assert (
                        usage.input_tokens,
                        usage.output_tokens,
                        usage.cache_read_tokens,
                        usage.cache_write_tokens,
                    ) == (17, 5, 3, 2)
                    assert recorder.events[-1] == {"kind": "reasoning"}
            finally:
                stream.release.set()
            assert stream.closed

        assert requests[0]["streaming"] is True
        assert requests[0]["stream_tokens"] is False
        assert not tools.events_sent
