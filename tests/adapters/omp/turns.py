"""Run real adapter turns and control their native peer callbacks."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from band.adapters.omp import OmpAdapter
from band.converters.omp import OmpSessionState
from band.core.protocols import AgentToolsProtocol
from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpEvent,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from band.testing import FakeAgentTools
from tests.adapters.omp.turnpeer import TurnControl
from tests.integrations.acp.conftest import make_platform_message


class NativeCallbacks:
    def __init__(self, client: OmpRpcClient) -> None:
        self.client = client

    async def event(self, event: OmpEvent) -> None:
        await self.client.request(
            TurnControl.EVENT, event=event.model_dump(mode="json", exclude_none=True)
        )

    async def tool(self, call: OmpHostToolCall) -> OmpToolResult:
        return OmpToolResult.model_validate(
            await self.client.request(
                TurnControl.TOOL, call=call.model_dump(mode="json")
            )
        )

    async def ui(self, request: OmpUIRequest) -> OmpUIResponse:
        response = await self.client.request(
            TurnControl.UI, request=request.model_dump(mode="json")
        )
        response.pop("type", None)
        response.pop("id", None)
        return OmpUIResponse.model_validate(response)


@dataclass(frozen=True)
class NativeTurn:
    adapter: OmpAdapter
    tools: FakeAgentTools
    client: OmpRpcClient
    callbacks: NativeCallbacks


NativeTurnFactory = Callable[..., AbstractAsyncContextManager[NativeTurn]]


async def run_turn(
    adapter: OmpAdapter,
    tools: AgentToolsProtocol,
    *,
    bootstrap: bool = True,
    history: OmpSessionState | None = None,
    room_id: str = "room-123",
) -> None:
    await adapter.run_judged_turn(
        msg=make_platform_message("Finish the background work", room_id=room_id),
        tools=tools,
        history=history or OmpSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=bootstrap,
        room_id=room_id,
    )


def reply_call(content: str) -> OmpHostToolCall:
    return OmpHostToolCall(
        id="host-call",
        toolCallId="reply",
        toolName="band_send_message",
        arguments={"content": content},
    )
