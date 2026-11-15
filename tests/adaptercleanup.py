"""Actual hosts and HTTP resources for adapter disposal regressions."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from band.agent import Agent
from band.core.protocols import FrameworkAdapter
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput
from band.integrations.uvicorn_server import (
    SERVER_START_TIMEOUT_S,
    SERVER_STOP_TIMEOUT_S,
    ManagedUvicornServer,
)
from band.platform.link import BandLink
from band.runtime.agentconfig import AgentConfig
from band.runtime.oneshot import OneShotInvoker
from band.runtime.platform_runtime import PlatformRuntime
from band.runtime.sessionconfig import SessionConfig
from band.testing import fake_phoenix_server
from tests.adapters.hold import Hold
from tests.adapters.lifecycle.runtime import ReplyingPlatform
from tests.runtime.helpers import AGENT_ID, rest_client_over

RESOURCE_PATH = "/resource"


class HostKind(StrEnum):
    AGENT = "agent"
    ONESHOT = "oneshot"


class CleanupPlatform(ReplyingPlatform):
    def answer(self, request: httpx.Request) -> httpx.Response:
        response = super().answer(request)
        if request.url.path.endswith("/me"):
            body = response.json()
            body["data"]["description"] = "Local lifecycle peer"
            return httpx.Response(response.status_code, json=body)
        return response


class PassiveAdapter(SimpleAdapter[Any]):
    async def on_message(self, *args: Any, **kwargs: Any) -> None:
        return None


class BareAdapter:
    def __init__(self) -> None:
        self.starts = 0

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        self.starts += 1

    async def on_event(self, inp: AgentInput) -> None:
        return None

    async def on_cleanup(self, room_id: str) -> None:
        return None


class BareTeardownAdapter(BareAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.stops = 0

    async def cleanup_all(self) -> None:
        self.stops += 1


class TeardownAdapter(PassiveAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.cleanup_calls = 0
        self.fail_cleanup = False
        self.cleanup_hold: Hold | None = None

    async def cleanup_all(self) -> None:
        self.cleanup_calls += 1
        if self.cleanup_hold is not None:
            await self.cleanup_hold.wait()
        if self.fail_cleanup:
            raise OSError("Framework teardown failed")


@dataclass
class Host:
    owner: Agent | OneShotInvoker
    link: BandLink
    platform: CleanupPlatform

    async def start(self) -> None:
        if isinstance(self.owner, Agent):
            await self.owner.start()
        else:
            await self.owner.startup()

    async def stop(self) -> None:
        if isinstance(self.owner, Agent):
            await self.owner.stop()
        else:
            await self.owner.shutdown()


@asynccontextmanager
async def host_for(
    adapter: FrameworkAdapter | SimpleAdapter[Any], kind: HostKind
) -> AsyncIterator[Host]:
    """Keep real WebSocket, scheduler and REST parsing active; no teardown mocks."""
    platform = CleanupPlatform()
    async with rest_client_over(platform.answer) as rest, fake_phoenix_server() as wire:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key", ws_url=wire.url)
        link.rest = rest
        if kind == HostKind.AGENT:
            runtime = PlatformRuntime(
                agent_id=AGENT_ID,
                api_key="test-key",
                ws_url=wire.url,
                config=AgentConfig(single_instance=False),
                session_config=SessionConfig(
                    enable_context_hydration=False, enable_working_state=False
                ),
            )
            runtime._link = link
            await runtime._fetch_agent_metadata()
            owner = Agent(runtime=runtime, adapter=adapter)
        else:
            owner = OneShotInvoker(link=link, adapter=adapter, agent_id=AGENT_ID)
        host = Host(owner, link, platform)
        try:
            yield host
        finally:
            await host.stop()


async def resource_response(request: Request) -> JSONResponse:
    return JSONResponse({"value": request.query_params.get("value", "open")})


@asynccontextmanager
async def resource_peer(
    routes: Sequence[Route] = (Route(RESOURCE_PATH, resource_response),),
) -> AsyncIterator[httpx.AsyncClient]:
    server = ManagedUvicornServer(
        Starlette(routes=list(routes)),
        host="127.0.0.1",
        port=0,
        start_timeout_s=SERVER_START_TIMEOUT_S,
        stop_timeout_s=SERVER_STOP_TIMEOUT_S,
    )
    await server.start()
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{server.bound_port}"
        ) as client:
            yield client
    finally:
        await server.stop()
