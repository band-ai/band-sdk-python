"""A real Copilot SDK constructor with only its process/RPC boundary replaced."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

if TYPE_CHECKING:
    from types import ModuleType

    from copilot import CopilotClient, ModelInfo


class CopilotProbeSDK:
    """Record launch options and control external SDK startup, listing and exit."""

    def __init__(self, module: ModuleType) -> None:
        self.client_type = module.CopilotClient
        self.models: list[ModelInfo] = []
        self.clients: list[CopilotClient] = []
        self.launches: list[dict[str, Any]] = []
        self.open_clients: set[CopilotClient] = set()
        self.started = asyncio.Event()
        self.release_start = asyncio.Event()
        self.hold_start = False
        self.ignore_start_cancellation = False
        self.start_error: Exception | None = None
        self.list_error: Exception | None = None
        self.close_failures = 0
        self.close_attempts = 0

    def factory(self, **options: Any) -> CopilotClient:
        # Calling the actual constructor rejects outdated dict-based SDK options.
        client = self.client_type(**options)
        self.clients.append(client)
        self.launches.append(options)

        async def start() -> None:
            self.open_clients.add(client)
            self.started.set()
            if self.hold_start:
                try:
                    await self.release_start.wait()
                except asyncio.CancelledError:
                    if not self.ignore_start_cancellation:
                        raise
                    await self.release_start.wait()
            if self.start_error is not None:
                raise self.start_error

        async def list_models() -> list[ModelInfo]:
            if self.list_error is not None:
                raise self.list_error
            return self.models

        async def stop() -> None:
            self.close_attempts += 1
            if self.close_failures:
                self.close_failures -= 1
                raise RuntimeError("Copilot process exit refused")
            self.open_clients.discard(client)

        client.start = AsyncMock(side_effect=start)
        client.list_models = AsyncMock(side_effect=list_models)
        client.stop = AsyncMock(side_effect=stop)
        client.create_session = AsyncMock(side_effect=AssertionError("model session"))
        return client
