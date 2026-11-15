"""Host tool and UI callbacks run concurrently and propagate cancellation or errors."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.ompwire import (
    OmpContent,
    OmpFrameType,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from tests.integrations.ompframes import UI_APPROVE, UI_DENY
from tests.integrations.omppeers.base import COMPLETE
from tests.integrations.omppeers.callbacks import HOST_CANCELLED_ID, UI_CANCELLED_ID
from tests.integrations.omprpc.callbacks import cancel_ui, echo_tool
from tests.integrations.omprpc.events import EventRecorder, follow_up_texts
from tests.integrations.omprpc.processes import owned_rpc_process
from tests.integrations.omprpc.spawn import ClientFactory
from tests.integrations.omprpc.wirelog import checkpoint_frames
from tests.integrations.ompscenarios import PeerCommand, PeerScenario


class TestHostToolAndUICallbacks:
    async def test_host_tools_and_ui_run_concurrently_even_before_prompt_acceptance(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory(PeerScenario.PARALLEL_CALLBACKS)
        observer = EventRecorder()
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
                await client.request(PeerCommand.CHECKPOINT)
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
            return OmpUIResponse(value=UI_APPROVE)

        async with asyncio.timeout(5):
            await client.prompt(
                "first", on_event=observer.on_event, on_tool=on_tool, on_ui=on_ui
            )
        assert {call.id for call in calls} == {"host-1", "host-2"}
        assert [(request.method, request.options) for request in approvals] == [
            ("select", [UI_APPROVE, UI_DENY])
        ]
        frames = await checkpoint_frames(client)
        result = next(frame for frame in frames if frame.get("id") == "host-2")
        assert result == {
            "type": OmpFrameType.HOST_TOOL_RESULT,
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
            "type": OmpFrameType.UI_RESPONSE,
            "id": "approval-1",
            "value": UI_APPROVE,
        }
        assert await follow_up_texts(client) == [COMPLETE]

    async def test_confirmed_ui_reply_reaches_the_peer(
        self, client_factory: ClientFactory
    ) -> None:
        client = await client_factory(PeerScenario.PARALLEL_CALLBACKS)
        observer = EventRecorder()

        async def on_ui(_request: OmpUIRequest) -> OmpUIResponse:
            return OmpUIResponse(confirmed=True)

        async with asyncio.timeout(5):
            await client.prompt(
                "first",
                on_event=observer.on_event,
                on_tool=echo_tool,
                on_ui=on_ui,
            )
        frames = await checkpoint_frames(client)
        ui_response = next(frame for frame in frames if frame.get("id") == "approval-1")
        assert ui_response == {
            "type": OmpFrameType.UI_RESPONSE,
            "id": "approval-1",
            "confirmed": True,
        }

    @pytest.mark.parametrize(
        ("scenario", "cancel_command", "target"),
        [
            (PeerScenario.HOST_CANCEL, PeerCommand.CANCEL_HOST, HOST_CANCELLED_ID),
            (PeerScenario.UI_CANCEL, PeerCommand.CANCEL_UI, UI_CANCELLED_ID),
        ],
    )
    async def test_native_cancel_targets_the_callback_and_keeps_the_next_turn_usable(
        self,
        client_factory: ClientFactory,
        scenario: PeerScenario,
        cancel_command: PeerCommand,
        target: str,
    ) -> None:
        client = await client_factory(scenario)
        observer = EventRecorder()
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
            return OmpUIResponse(value=UI_APPROVE)

        running = asyncio.create_task(
            client.prompt(
                "first", on_event=observer.on_event, on_tool=on_tool, on_ui=on_ui
            )
        )
        try:
            async with asyncio.timeout(5):
                await started.wait()
                await client.request(cancel_command)
                await cancelled.wait()
                await running
            frames = await checkpoint_frames(client)
            assert not any(frame.get("id") == target for frame in frames)
            assert await follow_up_texts(client) == [COMPLETE]
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    async def test_ui_callback_failure_fails_closed_and_reaps_the_process(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory(PeerScenario.UI_ERROR)
        process = owned_rpc_process(client)

        async def on_ui(request: OmpUIRequest) -> OmpUIResponse:
            raise ValueError("Unoffered permission choice")

        observer = EventRecorder()
        async with asyncio.timeout(6):
            with pytest.raises(ValueError, match="Unoffered permission choice"):
                await client.prompt(
                    "first",
                    on_event=observer.on_event,
                    on_tool=observer.on_tool,
                    on_ui=on_ui,
                )
        assert process.returncode is not None
        await client.start()
        assert await follow_up_texts(client) == [COMPLETE]

    async def test_tool_callback_exception_propagates_unchanged_and_reaps_the_process(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory(PeerScenario.PARALLEL_CALLBACKS)
        process = owned_rpc_process(client)
        observer = EventRecorder()
        sentinel = ValueError("Band delivery failed")

        async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
            raise sentinel

        async with asyncio.timeout(6):
            with pytest.raises(ValueError, match="Band delivery failed") as failed:
                await client.prompt(
                    "first",
                    on_event=observer.on_event,
                    on_tool=on_tool,
                    on_ui=cancel_ui,
                )
        assert failed.value is sentinel
        assert process.returncode is not None
        await client.start()
        assert await follow_up_texts(client) == [COMPLETE]
