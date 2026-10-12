"""A real agent runtime wired to a local Band platform: a real WebSocket
server and a stateful REST peer. Only the network is simulated."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from band.client.streaming import ControlMode
from band.platform.link import BandLink
from band.runtime.execution import Execution, ExecutionContext
from band.runtime.runtime import AgentRuntime
from band.runtime.types import SessionConfig
from band.testing import fake_phoenix_server
from band.testing.phoenix_server import FakePhoenixServer
from tests.runtime.helpers import rest_client_over
from tests.scenarios.observations import Observations
from tests.scenarios.peer import AGENT_ID, BandPeer, room_json
from tests.scenarios.script import BareExecution, Script

ROOMS_TOPIC = f"agent_rooms:{AGENT_ID}"
CONTROL_TOPIC = f"agent_control:{AGENT_ID}"

SESSION = SessionConfig(enable_context_hydration=False, enable_working_state=False)


class BandWorld:
    """Declarative verbs over one running agent and its platform."""

    def __init__(
        self,
        *,
        server: FakePhoenixServer,
        peer: BandPeer,
        script: Script,
        runtime: AgentRuntime,
        observations: Observations,
    ) -> None:
        self.server = server
        self.peer = peer
        self.script = script
        self.runtime = runtime
        self.observations = observations

    async def until(self, holds: Callable[[], bool]) -> None:
        await self.observations.until(holds)

    def story(self, room_id: str) -> list[str]:
        """Platform marks, turns, controls and cleanups for one room, in order."""
        return self.observations.story(room_id)

    async def until_told(self, room_id: str, entry: str) -> None:
        """Wait until ``entry`` appears in the room's story."""
        await self.until(lambda: entry in self.story(room_id))

    async def stop(self, *, timeout: float | None = None) -> bool:
        return await self.runtime.stop(timeout=timeout)

    def serving(self, room_id: str) -> bool:
        return self.runtime.rooms.serving(room_id)

    async def add_room(self, room_id: str) -> None:
        """The platform adds the agent to a room."""
        self.peer.room(room_id).member = True
        await self.server.push(ROOMS_TOPIC, "room_added", room_json(room_id))

    async def join(self, room_id: str) -> None:
        """Add the agent to a room and wait until it serves it."""
        await self.add_room(room_id)
        await self.until(lambda: self.serving(room_id))

    async def remove_room(self, room_id: str) -> None:
        self.peer.room(room_id).member = False
        await self.server.push(ROOMS_TOPIC, "room_removed", room_json(room_id))

    async def say(self, room_id: str, text: str) -> str:
        """A user posts ``text``; the platform stores it and pushes it live."""
        message = self.peer.post(room_id, text)
        await self.server.push(f"chat_room:{room_id}", "message_created", message)
        return message["id"]

    def say_unseen(self, room_id: str, text: str) -> str:
        """A user posts ``text`` while no live push reaches the agent."""
        return self.peer.post(room_id, text)["id"]

    async def control(
        self,
        mode: ControlMode,
        *,
        correlation_id: str,
        room_id: str | None = None,
    ) -> None:
        """The platform signals ``mode`` to one room, or to every room."""
        stopped = {ControlMode.STOP: True, ControlMode.PLAY: False}.get(mode)
        targets = [room_id] if room_id is not None else list(self.peer.rooms)
        if stopped is not None:
            for target in targets:
                self.peer.room(target).stopped = stopped
        payload: dict[str, Any] = {
            "mode": mode.value,
            "scope": "room" if room_id is not None else "agent",
            "agent_id": AGENT_ID,
            "room_id": room_id,
            "execution_id": f"execution-{correlation_id}",
            "correlation_id": correlation_id,
        }
        await self.server.push(CONTROL_TOPIC, "agent.control", payload)

    async def drop_connection(self) -> None:
        """Sever the socket; returns once the agent has reconciled its rooms."""
        listings = self.peer.listings
        await self.server.abort_connection()
        await self.until(lambda: self.peer.listings > listings)


def bare_room_factory(
    bare_rooms: frozenset[str], script: Script
) -> Callable[..., Execution]:
    """Serve ``bare_rooms`` with a ``BareExecution``, every other room with a
    real ``ExecutionContext`` running the script."""

    def build(
        room_id: str, link: BandLink, *, hub_room_id: str | None = None
    ) -> Execution:
        if room_id in bare_rooms:
            return BareExecution(room_id, script.observations)
        return ExecutionContext(
            room_id=room_id,
            link=link,
            on_execute=script.on_execute,
            config=SESSION,
            agent_id=AGENT_ID,
            hub_room_id=hub_room_id,
        )

    return build


@asynccontextmanager
async def band_world(
    *, bare_rooms: frozenset[str] = frozenset()
) -> AsyncIterator[BandWorld]:
    observations = Observations()
    peer = BandPeer(observations)
    script = Script(observations)
    factory = bare_room_factory(bare_rooms, script) if bare_rooms else None
    async with fake_phoenix_server() as server, rest_client_over(peer.answer) as rest:
        link = BandLink(
            agent_id=AGENT_ID,
            api_key="test-key",
            ws_url=server.url,
            rest_url="https://test.invalid",
        )
        link.rest = rest
        runtime = AgentRuntime(
            link,
            AGENT_ID,
            on_execute=script.on_execute,
            execution_factory=factory,
            session_config=SESSION,
            on_session_cleanup=script.cleanup,
            on_control=script.on_control,
        )
        link.on_control = runtime.handle_control
        await runtime.start()
        try:
            yield BandWorld(
                server=server,
                peer=peer,
                script=script,
                runtime=runtime,
                observations=observations,
            )
        finally:
            script.release_everything()
            peer.release_everything()
            await runtime.stop()
