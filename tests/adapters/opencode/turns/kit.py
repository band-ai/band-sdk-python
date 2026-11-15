"""OpenCode abort fault injection at the external client boundary."""

from __future__ import annotations

import asyncio

from tests.adapters.opencode.helpers import FakeOpencodeClient


class CancelOnAbortClient(FakeOpencodeClient):
    async def abort_session(self, session_id: str) -> None:
        raise asyncio.CancelledError
