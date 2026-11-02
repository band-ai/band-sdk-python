"""Hold a running agent's real memory save at its REST client."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from band.client.rest import AsyncRestClient
from band.platform.link import BandLink
from tests.e2e.baseline.toolkit.activity import ActivityCapture
from tests.e2e.baseline.toolkit.resultexchange import ExchangeWork


class HeldMemorySave:
    """Keep the memory save in flight until the test releases it.

    The SDK reads the visible activity when it sends a report, so a save that
    ends before an in-flight keep-alive returns is never reported.
    """

    def __init__(self, *, deadline_s: float) -> None:
        self.deadline_s = deadline_s
        self.save = ExchangeWork()

    def link(self, capture: ActivityCapture) -> Callable[..., BandLink]:
        """A link factory whose memory create request waits at the gate."""

        def build(**kwargs: Any) -> BandLink:
            link = capture.link(**kwargs)
            self.hold(link.rest)
            return link

        return build

    def hold(self, rest: AsyncRestClient) -> None:
        """Gate ``rest``'s memory create request; the real call runs on release."""
        memories = rest.agent_api_memories
        memories.create_agent_memory = self._held(memories.create_agent_memory)

    def _held(
        self, create: Callable[..., Awaitable[Any]]
    ) -> Callable[..., Awaitable[Any]]:
        async def held(*args: Any, **kwargs: Any) -> Any:
            if self.save.entered.is_set():
                raise RuntimeError("The held memory save accepts one request")
            self.save.entered.set()
            try:
                await self.save.release.wait()
                return await create(*args, **kwargs)
            finally:
                self.save.settled.set()

        return held

    async def wait(self, event: asyncio.Event, *, phase: str) -> None:
        try:
            async with asyncio.timeout(self.deadline_s):
                await event.wait()
        except TimeoutError:
            raise TimeoutError(f"Missing memory save barrier: {phase}") from None

    @asynccontextmanager
    async def releasing_on_exit(self) -> AsyncIterator[None]:
        try:
            yield
        finally:
            self.save.release.set()
            if self.save.entered.is_set():
                await self.wait(self.save.settled, phase="released save settled")
