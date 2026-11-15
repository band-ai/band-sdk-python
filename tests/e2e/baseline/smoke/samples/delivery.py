"""Deterministic failure recovery over the live platform transports."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import pytest

from band.core.protocols import AgentToolsProtocol
from band.core.settlement import SettlementResult
from band.core.simple_adapter import SimpleAdapter
from band.core.types import PlatformMessage
from band.platform.link import BandLink


class RecoveringAdapter(SimpleAdapter[Any]):
    """Fail the first accepted invocation, then settle the retry without an LLM."""

    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: Any,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        await self.on_turn(
            (msg,),
            tools,
            history,
            participants_msg,
            contacts_msg,
            is_session_bootstrap=is_session_bootstrap,
            room_id=room_id,
        )

    async def on_turn(
        self,
        messages: Sequence[PlatformMessage],
        tools: AgentToolsProtocol,
        history: Any,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        if not self._failed:
            self._failed = True
            raise RuntimeError("intentional first-attempt failure")
        await tools.no_reply("The accepted retry completed.")


class LostFailureResponse:
    """Hide one accepted response; hold the next real write until explicitly released."""

    def __init__(self, link: BandLink, *, deadline_s: float) -> None:
        self._write = link.mark_failed_result
        self._deadline_s = deadline_s
        self._lost = False
        self._retrying = asyncio.Event()
        self._released = asyncio.Event()

    async def write(
        self, room_id: str, message_id: str, error: str
    ) -> SettlementResult:
        if not self._lost:
            result = await self._write(room_id, message_id, error)
            assert result is SettlementResult.ACCEPTED, (
                f"the real platform did not accept the first FAILED write: {result}"
            )
            self._lost = True
            return SettlementResult.UNCONFIRMED
        self._retrying.set()
        await self._released.wait()
        return await self._write(room_id, message_id, error)

    async def wait_for_retry(self) -> None:
        async with asyncio.timeout(self._deadline_s):
            await self._retrying.wait()

    def release(self) -> None:
        self._released.set()


@contextmanager
def lose_failed_response(
    link: BandLink, *, deadline_s: float
) -> Iterator[LostFailureResponse]:
    fault = LostFailureResponse(link, deadline_s=deadline_s)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(link, "mark_failed_result", fault.write)
        try:
            yield fault
        finally:
            fault.release()
