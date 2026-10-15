"""Native MCP descriptors and callback barriers for ACP resolver tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from acp.schema import (
    EnvVariable,
    HttpHeader,
    HttpMcpServer,
    McpServerStdio,
    SseMcpServer,
)

SECRET = "s3cret-token"


def http_server(
    name: str = "repo", url: str = "http://127.0.0.1:9/mcp"
) -> HttpMcpServer:
    return HttpMcpServer(
        type="http",
        name=name,
        url=url,
        headers=[HttpHeader(name="Authorization", value=f"Bearer {SECRET}")],
    )


def sse_server(name: str = "events") -> SseMcpServer:
    return SseMcpServer(
        type="sse",
        name=name,
        url="http://127.0.0.1:9/sse",
        headers=[HttpHeader(name="Authorization", value=f"Bearer {SECRET}")],
    )


def stdio_server(name: str = "tools") -> McpServerStdio:
    return McpServerStdio(
        name=name,
        command="mcp-tools",
        args=["mcp"],
        env=[EnvVariable(name="TOKEN", value=SECRET)],
    )


def server_names(servers: Sequence[Any]) -> list[str]:
    return [server.name for server in servers]


class Resolver:
    """Record the workspace of each resolution and return scripted servers."""

    def __init__(self, *servers: Any) -> None:
        self.servers: list[Any] = list(servers)
        self.workspaces: list[str] = []

    async def __call__(self, workspace: str) -> Sequence[Any]:
        self.workspaces.append(workspace)
        return self.servers


class HeldResolver(Resolver):
    """Hold the first resolution, then finish cancellation only when released.

    ``swallow`` clears the task's cancellation request, as a callback that
    catches ``CancelledError`` and calls ``uncancel()`` would."""

    def __init__(
        self,
        *,
        self_cancel: bool = False,
        wait_for_cancel: bool = False,
        swallow: bool = False,
        request: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(http_server())
        self.entered = asyncio.Event()
        self.settling = asyncio.Event()
        self.settle = asyncio.Event()
        self.cancellations = 0
        self.log: list[str] = []
        self.self_cancel = self_cancel
        self.wait_for_cancel = wait_for_cancel
        self.request = request
        self.swallow = swallow

    async def __call__(self, workspace: str) -> Sequence[Any]:
        self.workspaces.append(workspace)
        if len(self.workspaces) > 1:
            self.log.append("second call")
            return self.servers
        task = asyncio.current_task()
        assert task is not None
        if self.self_cancel:
            task.cancel()
        self.entered.set()
        if self.request is None or self.self_cancel or self.wait_for_cancel:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancellations += 1
                if self.swallow:
                    task.uncancel()
        if self.request is not None:
            await self.request()
            self.log.append("request returned")
        self.settling.set()
        try:
            await self.settle.wait()
        except asyncio.CancelledError:
            self.cancellations += 1
            self.log.append("settlement interrupted")
            raise
        self.log.append("settled")
        return self.servers
