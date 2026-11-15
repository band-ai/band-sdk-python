"""External Codex stream faults delivered after native turn admission."""

from __future__ import annotations

from band.integrations.codex import RpcEvent
from tests.adapters.codexturns import FakeCodexClient


class RpcErrorClient(FakeCodexClient):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def recv_event(self, timeout_s: float | None = None) -> RpcEvent:
        await self.turn_started.wait()
        raise self.error
