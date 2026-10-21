"""Host tool and UI callbacks run concurrently and propagate cancellation or errors."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.ompwire import (
    OmpContent,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from tests.integrations.omprpc.kit import (
    ClientFactory,
    Observer,
    checkpoint_frames,
    second_prompt,
)


class TestHostToolAndUICallbacks:
    async def test_host_tools_and_ui_run_concurrently_even_before_prompt_acceptance(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory("callbacks")
        observer = Observer()
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        calls: list[OmpHostToolCall] = []
        approvals: list[OmpUIRequest] = []

        async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
            calls.append(call)
            if call.arguments["number"] == 1:
                first_started.set()
                await release_first.wait()
            else:
                await first_started.wait()
                await client.request("checkpoint")
                release_first.set()
            return OmpToolResult(
                content=[
                    OmpContent(type="text", text=call.toolCallId),
                    OmpContent(type="image", data="aW1hZ2U=", mimeType="image/png"),
                ],
                details={"number": call.arguments["number"]},
                is_error=call.arguments["number"] == 2,
            )

        async def on_ui(request: OmpUIRequest) -> OmpUIResponse:
            approvals.append(request)
            return OmpUIResponse(value="Approve")

        async with asyncio.timeout(5):
            await client.prompt(
                "first", on_event=observer.on_event, on_tool=on_tool, on_ui=on_ui
            )
        assert {call.id for call in calls} == {"host-1", "host-2"}
        assert [(request.method, request.options) for request in approvals] == [
            ("select", ["Approve", "Deny"])
        ]
        frames = await checkpoint_frames(client)
        result = next(frame for frame in frames if frame.get("id") == "host-2")
        assert result == {
            "type": "host_tool_result",
            "id": "host-2",
            "result": {
                "content": [
                    {"type": "text", "text": "call-2"},
                    {"type": "image", "data": "aW1hZ2U=", "mimeType": "image/png"},
                ],
                "details": {"number": 2},
            },
            "isError": True,
        }
        ui_response = next(frame for frame in frames if frame.get("id") == "approval-1")
        assert ui_response == {
            "type": "extension_ui_response",
            "id": "approval-1",
            "value": "Approve",
        }
        await second_prompt(client)

    @pytest.mark.parametrize("kind", ["host", "ui"])
    async def test_native_cancel_targets_the_callback_and_keeps_the_next_turn_usable(
        self, client_factory: ClientFactory, kind: str
    ) -> None:
        client = await client_factory(f"{kind}-cancel")
        observer = Observer()
        started = asyncio.Event()
        cancelled = asyncio.Event()
        forever = asyncio.Event()

        async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
            started.set()
            try:
                await forever.wait()
            finally:
                cancelled.set()
            return OmpToolResult(content=[])

        async def on_ui(request: OmpUIRequest) -> OmpUIResponse:
            started.set()
            try:
                await forever.wait()
            finally:
                cancelled.set()
            return OmpUIResponse(value="Approve")

        running = asyncio.create_task(
            client.prompt(
                "first", on_event=observer.on_event, on_tool=on_tool, on_ui=on_ui
            )
        )
        try:
            async with asyncio.timeout(5):
                await started.wait()
                await client.request(f"cancel-{kind}")
                await cancelled.wait()
                await running
            frames = await checkpoint_frames(client)
            target = "host-cancelled" if kind == "host" else "ui-cancelled"
            assert not any(frame.get("id") == target for frame in frames)
            await second_prompt(client)
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    async def test_ui_callback_failure_fails_closed_and_reaps_the_process(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory("ui-error")
        process = client._proc
        assert process is not None

        async def on_ui(request: OmpUIRequest) -> OmpUIResponse:
            raise ValueError("Unoffered permission choice")

        observer = Observer()
        async with asyncio.timeout(6):
            with pytest.raises(ValueError, match="Unoffered permission choice"):
                await client.prompt(
                    "first",
                    on_event=observer.on_event,
                    on_tool=observer.on_tool,
                    on_ui=on_ui,
                )
        assert process.returncode is not None

    async def test_tool_callback_exception_propagates_unchanged_and_reaps_the_process(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory("callbacks")
        process = client._proc
        assert process is not None
        observer = Observer()
        sentinel = ValueError("Band delivery failed")

        async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
            raise sentinel

        async with asyncio.timeout(6):
            with pytest.raises(ValueError, match="Band delivery failed") as failed:
                await client.prompt(
                    "first",
                    on_event=observer.on_event,
                    on_tool=on_tool,
                    on_ui=observer.on_ui,
                )
        assert failed.value is sentinel
        assert process.returncode is not None
        await client.start()
        await second_prompt(client)
