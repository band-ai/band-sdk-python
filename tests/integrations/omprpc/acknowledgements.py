"""Request native stop after a successful tool acknowledgement."""

from __future__ import annotations

from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import OmpHostToolCall, OmpToolResult
from tests.integrations.omppeers.acknowledgements import ADMIT_STOP_SIBLING
from tests.integrations.omprpc.events import EventRecorder


class StoppingObserver(EventRecorder):
    def __init__(self) -> None:
        super().__init__()
        self.client: OmpRpcClient | None = None
        self.admit_sibling = False

    async def on_tool(self, call: OmpHostToolCall) -> OmpToolResult:
        assert self.client is not None
        self.client.stop_after_tool(call.id)
        if self.admit_sibling:
            await self.client.request(ADMIT_STOP_SIBLING)
        return OmpToolResult(content=[])
