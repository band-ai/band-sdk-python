"""Turn-outcome probes through OMP's real native host-tool dispatch."""

from __future__ import annotations

import tempfile
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, patch

from pydantic import JsonValue

from band.integrations.ompwire import (
    OMP_ASSISTANT_ROLE,
    OmpCommandName,
    OmpEvent,
    OmpEventType,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.turnprobes import (
    TurnOutcomeProbe,
    TurnScript,
    turn_input,
)


class ScriptedOmpClient:
    """Replace only the external OMP process, keeping the adapter's turn path real."""

    def __init__(self, script: TurnScript) -> None:
        self.script = script
        self.stop_after_tool_id: str | None = None

    def stop_after_tool(self, call_id: str) -> None:
        if self.stop_after_tool_id is None:
            self.stop_after_tool_id = call_id

    async def start(self) -> None:
        pass

    async def request(
        self, command: str, **parameters: JsonValue
    ) -> dict[str, JsonValue]:
        match command:
            case OmpCommandName.GET_STATE:
                return {"sessionId": "conformance-session", "isSettled": True}
            case OmpCommandName.SET_HOST_TOOLS:
                assert parameters["tools"], (
                    "a native session must advertise its host tools"
                )
                return {}
            case "set_ask_dialog":
                assert parameters == {"enabled": True}
                return {"enabled": True}
            case _:
                raise AssertionError(f"Unexpected OMP command: {command}")

    async def prompt(
        self,
        message: str,
        *,
        on_event: Callable[[OmpEvent], Awaitable[None]],
        on_tool: Callable[[OmpHostToolCall], Awaitable[OmpToolResult]],
        on_ui: Callable[[OmpUIRequest], Awaitable[OmpUIResponse]],
    ) -> None:
        del message, on_ui
        self.stop_after_tool_id = None
        for index, call in enumerate(self.script.tool_calls):
            request_id = f"request-{index}"
            await on_tool(
                OmpHostToolCall(
                    id=request_id,
                    toolCallId=f"tool-{index}",
                    toolName=call.name,
                    arguments=call.arguments,
                )
            )
            if self.stop_after_tool_id == request_id:
                return
        if self.script.final_text:
            await on_event(
                OmpEvent.model_validate(
                    {
                        "type": OmpEventType.MESSAGE_END,
                        "message": {
                            "role": OMP_ASSISTANT_ROLE,
                            "content": [
                                {"type": "text", "text": self.script.final_text}
                            ],
                            "stopReason": "stop",
                        },
                    }
                )
            )

    async def close(self) -> None:
        pass


async def run_omp(script: TurnScript, tools: FakeAgentTools) -> None:
    from band.adapters.omp import (  # noqa: PLC0415 -- the omp extra is absent from other adapter lanes
        OmpAdapter,
        OmpAdapterConfig,
    )

    with tempfile.TemporaryDirectory(prefix="band-conformance-omp-") as workspace:
        adapter = OmpAdapter(OmpAdapterConfig(cwd=workspace))
        runtime = ScriptedOmpClient(script)
        with (
            patch("band.adapters.omp.OmpRpcClient", return_value=runtime),
            patch("band.adapters.omp.omp_mcp_ready", new=AsyncMock()),
        ):
            await adapter.on_started("Agent", "A coding agent")
            try:
                await adapter.on_event(turn_input(tools))
            finally:
                await adapter.cleanup_all()


PROBES: dict[str, TurnOutcomeProbe] = {
    "omp": TurnOutcomeProbe(run=run_omp, relays=False),
}
