"""Reader-spawned host callbacks do not inherit the prompt task's context."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar

from band.integrations.ompwire import (
    OmpContent,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from tests.integrations.ompframes import UI_APPROVE
from tests.integrations.omprpc.events import EventRecorder
from tests.integrations.omprpc.spawn import ClientFactory
from tests.integrations.ompscenarios import PeerScenario

PROMPT_TOKEN = ContextVar("omp_rpc_prompt_token", default="reader")


class TestCallbackWithoutPromptContext:
    async def test_ui_and_tool_callbacks_keep_the_reader_token(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory(PeerScenario.PARALLEL_CALLBACKS)
        observer = EventRecorder()
        seen: list[str] = []

        async def on_ui(_request: OmpUIRequest) -> OmpUIResponse:
            seen.append(PROMPT_TOKEN.get())
            return OmpUIResponse(value=UI_APPROVE)

        async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
            seen.append(PROMPT_TOKEN.get())
            return OmpToolResult(content=[OmpContent(type="text", text=call.toolName)])

        token = PROMPT_TOKEN.set("prompt")
        try:
            async with asyncio.timeout(5):
                await client.prompt(
                    "first",
                    on_event=observer.on_event,
                    on_tool=on_tool,
                    on_ui=on_ui,
                )
        finally:
            PROMPT_TOKEN.reset(token)
        assert seen
        assert set(seen) == {"reader"}
