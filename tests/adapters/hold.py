"""A barrier a test parks a provider step at, shared by every provider's fakes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import TracebackType


@dataclass
class Hold:
    """Parks the turn at this step: ``async with hold`` waits for the turn to
    reach it and lets the turn continue on exit."""

    reached: asyncio.Event = field(default_factory=asyncio.Event)
    released: asyncio.Event = field(default_factory=asyncio.Event)

    async def __aenter__(self) -> None:
        await self.reached.wait()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.released.set()

    async def wait(self) -> None:
        """Signal the step is reached and park until the test releases it."""
        self.reached.set()
        await self.released.wait()
