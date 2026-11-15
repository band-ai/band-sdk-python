"""Request native stop after a successful tool acknowledgement."""

from __future__ import annotations

from band.integrations.omprpc import OmpRpcClient
from band.integrations.omprpc.prompt import OmpCallbackKind
from band.integrations.ompwire import OmpHostToolCall, OmpToolResult
from tests.integrations.omppeers.acknowledgements import (
    ADMIT_STOP_SIBLING,
    STOP_SIBLING_ID,
)
from tests.integrations.omprpc.events import EventRecorder


class StoppingObserver(EventRecorder):
    def __init__(self) -> None:
        super().__init__()
        self.client: OmpRpcClient | None = None
        self.admit_sibling = False
        self.sibling_parked = False

    async def on_tool(self, call: OmpHostToolCall) -> OmpToolResult:
        assert self.client is not None
        self.client.stop_after_tool(call.id)
        if self.admit_sibling:
            await self.client.request(ADMIT_STOP_SIBLING)
            prompt = self.client._turn
            assert prompt is not None
            assert (OmpCallbackKind.TOOL, STOP_SIBLING_ID) in prompt.callbacks
            assert not prompt.stop_resolved.is_set()
            self.sibling_parked = True
        return OmpToolResult(content=[])
