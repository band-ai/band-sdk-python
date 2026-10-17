"""Observable lifespan owners for application shutdown tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Self

from starlette.applications import Starlette


class Participant:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.started = asyncio.Event()

    async def __aenter__(self) -> Self:
        self.events.append("agent-start")
        return self

    async def __aexit__(self, *args: object) -> None:
        self.events.append("agent-stop")

    async def run_forever(self, *, install_signal_handlers: bool) -> None:
        assert not install_signal_handlers
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.events.append("agent-wait-cancelled")


class Bridge:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.started = asyncio.Event()

    async def run(self, *, proxy: str) -> None:
        self.events.append("bridge-start-" + proxy)
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.events.append("bridge-cancelled")


class Gateway:
    def __init__(self, events: list[str]) -> None:
        @asynccontextmanager
        async def lifespan(app: Starlette) -> AsyncGenerator[None]:
            events.append("gateway-start")
            try:
                yield
            finally:
                events.append("gateway-stop")

        self.app = Starlette(lifespan=lifespan)
