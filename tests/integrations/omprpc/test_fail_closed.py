"""Dispatcher fail-closed paths keep OMP from hanging on unmatched host frames."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import JsonValue

from band.integrations.omprpc import OmpProtocolError
from band.integrations.ompwire import (
    OmpFrameType,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from tests.integrations.omprpc.kit import (
    ClientFactory,
    Observer,
    checkpoint_frames,
)


class TestFailClosedDispatch:
    async def test_host_tool_without_an_active_prompt_fails_the_transport(
        self, client_factory: ClientFactory
    ) -> None:
        client = await client_factory("basic")
        async with asyncio.timeout(6):
            with pytest.raises(OmpProtocolError, match="without an active prompt"):
                await client.request("orphan-tool")

    async def test_ui_cancel_without_target_id_fails_the_transport(
        self, client_factory: ClientFactory
    ) -> None:
        client = await client_factory("basic")
        async with asyncio.timeout(6):
            with pytest.raises(OmpProtocolError, match="missing targetId"):
                await client.request("orphan-ui-cancel")

    async def test_interactive_ui_without_an_active_prompt_is_cancelled(
        self, client_factory: ClientFactory
    ) -> None:
        client = await client_factory("basic")
        async with asyncio.timeout(5):
            await client.request("orphan-ui")
            frames = await checkpoint_frames(client)
        orphan = next(frame for frame in frames if frame.get("id") == "ui-orphan")
        assert orphan == {
            "type": OmpFrameType.UI_RESPONSE,
            "id": "ui-orphan",
            "cancelled": True,
        }

    async def test_ui_notifications_do_not_start_a_callback(
        self, client_factory: ClientFactory
    ) -> None:
        client = await client_factory("ui-notify")
        observer = Observer()
        seen: list[OmpUIRequest] = []

        async def on_ui(request: OmpUIRequest) -> OmpUIResponse:
            seen.append(request)
            return await observer.on_ui(request)

        async with asyncio.timeout(5):
            await client.prompt(
                "first",
                on_event=observer.on_event,
                on_tool=observer.on_tool,
                on_ui=on_ui,
            )
        assert seen == []

    async def test_reused_callback_id_fails_the_transport(
        self, client_factory: ClientFactory
    ) -> None:
        client = await client_factory("reused-callback")
        observer = Observer()
        callback_entered = asyncio.Event()
        release_callback = asyncio.Event()

        async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
            callback_entered.set()
            await release_callback.wait()
            return await observer.on_tool(call)

        prompt = asyncio.create_task(
            client.prompt(
                "first",
                on_event=observer.on_event,
                on_tool=on_tool,
                on_ui=observer.on_ui,
            )
        )
        repeat: asyncio.Task[dict[str, JsonValue]] | None = None
        try:
            async with asyncio.timeout(6):
                await callback_entered.wait()
                repeat = asyncio.create_task(client.request("repeat-callback"))
                with pytest.raises(
                    OmpProtocolError, match="callback request id was reused"
                ):
                    await prompt
                with pytest.raises(OmpProtocolError):
                    await repeat
        finally:
            release_callback.set()
            prompt.cancel()
            if repeat is not None:
                repeat.cancel()
                await asyncio.gather(prompt, repeat, return_exceptions=True)
            else:
                await asyncio.gather(prompt, return_exceptions=True)

    async def test_request_rejects_id_and_type_overrides(
        self, client_factory: ClientFactory
    ) -> None:
        client = await client_factory("basic")
        with pytest.raises(ValueError, match="must not override"):
            await client.request("checkpoint", id="stolen")
        with pytest.raises(ValueError, match="must not override"):
            await client.request("checkpoint", type="prompt")
        async with asyncio.timeout(5):
            checkpoint = await client.request("checkpoint")
        assert "pid" in checkpoint
