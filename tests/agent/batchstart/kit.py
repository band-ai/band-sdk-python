"""A real Agent started against a loopback Band platform with a batch-capable adapter."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any

import httpx

from band.client.streaming import DeliveryStatus
from band.core.exceptions import InputNotAccepted
from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import BatchInputAdapter
from band.core.types import AgentInput
from band.core.types import PlatformMessage as AdapterMessage
from band.platform.event import PlatformEvent
from band.preprocessing.default import DefaultPreprocessor
from band.runtime.execution import ExecutionContext
from band.runtime.messages import PlatformMessage
from band.runtime.sessionconfig import SessionConfig
from band.testing.phoenix_server import FakePhoenixServer
from tests.scenarios.peer import API_PREFIX, BandPeer

ROOM_ID = "room-1"
DESCRIPTION = "A batch-capable agent"
PROJECTED_PREFIX = "projected: "
CONTROL_CONTENT = "approve"

# Short enough that a refused input is retried within the test's patience.
RETRY_SECONDS = 0.05

SESSION = SessionConfig(
    consume_all_queued=True,
    enable_context_hydration=False,
    enable_working_state=False,
    idle_resync_seconds=RETRY_SECONDS,
    idle_resync_max_seconds=RETRY_SECONDS,
)


@dataclass(frozen=True)
class Turn:
    """One accepted model turn: the originals it ran over and whether the room
    still owed its first context."""

    ids: tuple[str, ...]
    bootstrap: bool


class QueuedAdapter(BatchInputAdapter[Any]):
    """A batch adapter that refuses its first offer and accepts every later one."""

    def __init__(self) -> None:
        super().__init__()
        self.refused: list[tuple[str, ...]] = []
        self.turns: list[Turn] = []
        self.contents: list[tuple[str, ...]] = []

    async def on_turn(
        self,
        messages: Sequence[AdapterMessage],
        tools: AgentToolsProtocol,
        history: Any,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        if not self.refused:
            self.refused.append(tuple(m.id for m in messages))
            raise InputNotAccepted("busy")
        self.turns.append(Turn(tuple(m.id for m in messages), is_session_bootstrap))
        self.contents.append(tuple(m.content for m in messages))
        await tools.no_reply("scripted")


class IdentityPreprocessor(DefaultPreprocessor):
    """A custom preprocessor that inherits the stock projection."""


class BareBatchAdapter:
    """A batch-capable framework adapter with no control handling."""

    supports_batch_input = True

    def __init__(self) -> None:
        self.turns: list[tuple[str, ...]] = []

    async def on_event(self, inp: AgentInput) -> None:
        await self.on_messages(inp)

    async def on_messages(self, inp: AgentInput) -> None:
        self.turns.append(tuple(message.id for message in inp.messages))
        await inp.tools.no_reply("scripted")

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        pass

    async def on_cleanup(self, room_id: str) -> None:
        pass


class ProjectingPreprocessor(DefaultPreprocessor):
    @staticmethod
    def message(
        ctx: ExecutionContext, event: PlatformEvent, agent_id: str
    ) -> PlatformMessage | None:
        message = DefaultPreprocessor.message(ctx, event, agent_id)
        return (
            replace(message, content=f"{PROJECTED_PREFIX}{message.content}")
            if message is not None
            else None
        )


class ProjectedControlAdapter(QueuedAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.controls: list[str] = []

    def is_control_message(self, msg: AdapterMessage) -> bool:
        return msg.content == f"{PROJECTED_PREFIX}{CONTROL_CONTENT}"

    async def try_handle_control(
        self, msg: AdapterMessage, tools: AgentToolsProtocol
    ) -> bool:
        if not self.is_control_message(msg):
            return False
        self.controls.append(msg.content)
        return True


class AgentPeer(BandPeer):
    """A ``BandPeer`` whose agent profile carries the description ``Agent.start`` needs."""

    async def answer(self, request: httpx.Request) -> httpx.Response:
        response = await super().answer(request)
        if request.url.path != f"{API_PREFIX}me":
            return response
        profile = response.json()
        profile["data"]["description"] = DESCRIPTION
        return httpx.Response(200, json=profile)


@dataclass
class Platform:
    """The platform an agent is started against."""

    server: FakePhoenixServer
    peer: AgentPeer


async def wait_for_processed_messages(
    peer: AgentPeer, message_ids: Iterable[str]
) -> None:
    """Wait until every expected room message is processed."""
    expected_ids = set(message_ids)
    if not expected_ids:
        raise ValueError("At least one message ID is required")

    def expected_messages_are_processed() -> bool:
        statuses = peer.room(ROOM_ID).status
        return expected_ids <= statuses.keys() and all(
            statuses[message_id] == DeliveryStatus.PROCESSED
            for message_id in expected_ids
        )

    await peer.observations.until(expected_messages_are_processed)
