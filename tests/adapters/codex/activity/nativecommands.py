"""A protocol peer that acknowledges selective stop before native completion."""

from __future__ import annotations

import asyncio
from typing import Any

from band.integrations.codex.types import CodexRequestMethod
from tests.adapters.codexturns import FakeCodexClient
from tests.adapters.hold import Hold


class CommandClient(FakeCodexClient):
    def __init__(
        self, *, stop_error: Exception | None = None, closing: Hold | None = None
    ) -> None:
        super().__init__()
        self.stop_error = stop_error
        self.closing = closing
        self.stops: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        retry_on_overload: bool = True,
    ) -> dict[str, Any]:
        if method == CodexRequestMethod.TERMINAL_TERMINATE:
            assert params is not None
            self.stops.put_nowait(params)
            if self.stop_error is not None:
                raise self.stop_error
            return {"terminated": True}
        return await super().request(
            method, params, retry_on_overload=retry_on_overload
        )

    async def close(self) -> None:
        if self.closing is not None:
            await self.closing.wait()
        await super().close()
