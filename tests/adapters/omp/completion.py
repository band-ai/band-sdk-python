"""Observe real prompt completion and recovery."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from band.adapters.omp import OmpAdapter
from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpEvent,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from band.testing import FakeAgentTools
from tests.integrations.omprpc.callbacks import StubbornCallbackObserver
from tests.integrations.omprpc.completion import (
    CALLBACK_SETTLEMENT_BUDGET_S,
    COMPLETION_BUDGET_S,
    state_request_count,
)
from tests.integrations.ompscenarios import PeerCommand, PeerScenario
from tests.toolkit.timecontrol import ControlledClock


class RefreshClient(OmpRpcClient):
    """Observe successful real prompt completion before the adapter refresh."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        lose_refresh: bool,
        env: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(command, env=env)
        self.completion_timeout_s = COMPLETION_BUDGET_S
        self.prompt_completed = asyncio.Event()
        self.state_requests_after_prompt = 0
        self.lose_refresh = lose_refresh

    async def prompt(
        self,
        message: str,
        *,
        on_event: Callable[[OmpEvent], Awaitable[None]],
        on_tool: Callable[[OmpHostToolCall], Awaitable[OmpToolResult]],
        on_ui: Callable[[OmpUIRequest], Awaitable[OmpUIResponse]],
    ) -> None:
        await super().prompt(message, on_event=on_event, on_tool=on_tool, on_ui=on_ui)
        self.state_requests_after_prompt = await state_request_count(self)
        if self.lose_refresh:
            await self.request(PeerCommand.DROP_STATE_RESPONSES)
        self.prompt_completed.set()


class RefreshClients:
    """Launch a lossy peer once, then a healthy peer for explicit recovery."""

    def __init__(self) -> None:
        self.clients: list[RefreshClient] = []
        self.created: asyncio.Queue[RefreshClient] = asyncio.Queue()

    def __call__(
        self, command: Sequence[str], *, env: Mapping[str, str] | None = None
    ) -> RefreshClient:
        launch = list(command)
        if self.clients:
            launch[launch.index("--scenario") + 1] = PeerScenario.COMPLETION_REPLY
        client = RefreshClient(launch, lose_refresh=not self.clients, env=env)
        self.clients.append(client)
        self.created.put_nowait(client)
        return client


class HoldInput(BaseModel):
    """Accept the zero-field cancellation-resistant hold tool input."""


class StubbornAdapterTool(StubbornCallbackObserver):
    """Keep real custom-tool work alive until its owner releases it."""

    async def hold(self, _input: HoldInput) -> str:
        await self.hold_callback()
        return "released"


class RetirementClients:
    """Launch a stubborn callback peer, then healthy peers for adapter retries."""

    def __init__(self) -> None:
        self.clients: list[OmpRpcClient] = []
        self.created: asyncio.Queue[OmpRpcClient] = asyncio.Queue()
        self.plugins: list[Path] = []

    def __call__(
        self, command: Sequence[str], *, env: Mapping[str, str] | None = None
    ) -> OmpRpcClient:
        launch = list(command)
        if self.clients:
            launch[launch.index("--scenario") + 1] = PeerScenario.COMPLETION_REPLY
        client = OmpRpcClient(launch, env=env)
        client.completion_timeout_s = COMPLETION_BUDGET_S
        client.callback_exit_timeout_s = CALLBACK_SETTLEMENT_BUDGET_S
        self.plugins.append(Path(launch[launch.index("--plugin-dir") + 1]))
        self.clients.append(client)
        self.created.put_nowait(client)
        return client


@dataclass(frozen=True)
class SurvivingToolTurn:
    adapter: OmpAdapter
    clients: RetirementClients
    hold: StubbornAdapterTool
    runtime: OmpRpcClient
    running: asyncio.Task[None]
    tools: FakeAgentTools


@dataclass(frozen=True)
class RetirementTurn(SurvivingToolTurn):
    clock: ControlledClock


SurvivingToolContext = AbstractAsyncContextManager[SurvivingToolTurn]
RetirementContext = AbstractAsyncContextManager[RetirementTurn]
