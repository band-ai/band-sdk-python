"""Shared room-startup doubles for native Codex MCP tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.integrations.codex.mcp import (
    CodexMcpHttpServer,
    CodexMcpLaunch,
    CodexMcpServer,
)
from band.integrations.codex.types import CodexRequestMethod
from tests.adapters.codexturns import FakeCodexClient


def ready_row(name: str = "private") -> dict[str, Any]:
    return {
        "name": name,
        "serverInfo": {"name": "fixture", "version": "1"},
        "serverCapabilities": {},
        "toolsError": None,
        "runtimeStatus": None,
        "tools": {},
        "authStatus": "bearerToken",
    }


class StatusClient(FakeCodexClient):
    def __init__(
        self,
        pages: list[dict[str, Any]] | None = None,
        checkpoint: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__()
        self.pages = list(pages if pages is not None else [{"data": [ready_row()]}])
        self.checkpoint = checkpoint

    async def pause(self, stage: str) -> None:
        if self.checkpoint is not None:
            await self.checkpoint(stage)

    async def connect(self) -> None:
        await self.pause("connect")
        await super().connect()

    async def initialize(self, **kwargs: Any) -> dict[str, Any]:
        await self.pause("initialize")
        return await super().initialize(**kwargs)

    async def request(
        self, method: str, params: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        if method == "mcpServerStatus/list":
            await self.pause("readiness")
            self.requests.append((method, params or {}))
            return self.pages.pop(0)
        await self.pause(
            "skills" if method == CodexRequestMethod.SKILLS_EXTRA_ROOTS_SET else "model"
        )
        return await super().request(method, params, **kwargs)


def mcp_adapter(
    tmp_path: Path,
    clients: list[FakeCodexClient],
    resolver: Callable[[str], Awaitable[Mapping[str, CodexMcpServer]]],
    **config: Any,
) -> tuple[CodexAdapter, list[CodexMcpLaunch | None]]:
    adapter = CodexAdapter(
        CodexAdapterConfig(
            workspace_for_room=lambda room: str(tmp_path / room), **config
        ),
        resolve_mcp_servers=resolver,
    )
    launches: list[CodexMcpLaunch | None] = []

    def build(_room: object, launch: CodexMcpLaunch | None) -> FakeCodexClient:
        launches.append(launch)
        return clients[len(launches) - 1]

    adapter._build_client = build
    return adapter, launches


async def start(adapter: CodexAdapter, room_id: str = "a") -> None:
    room = adapter._room_client(room_id)
    adapter._active_room.set(room_id)
    try:
        async with room.retained.use():
            pass
    except BaseException:
        # A direct helper caller observes the owned recovery, not just its waiter.
        if room.retained._transition is not None:
            await asyncio.wait({room.retained._transition})
        with suppress(RuntimeError):
            await room.retained._settle()
        raise


async def private_servers(_workspace: str) -> Mapping[str, CodexMcpServer]:
    return {
        "private": CodexMcpHttpServer(
            "http://localhost/mcp", bearer_token="secret-sentinel"
        )
    }


class DuplicateServers(Mapping[str, Any]):
    """A caller mapping can yield the same name twice from items()."""

    def __getitem__(self, key: str) -> Any:
        if key != "private":
            raise KeyError(key)
        return CodexMcpHttpServer("http://localhost/mcp")

    def __iter__(self) -> Iterator[str]:
        return iter(("private", "private"))

    def __len__(self) -> int:
        return 2


class SseServer:
    transport = "sse"
    url = "http://localhost/sse"
