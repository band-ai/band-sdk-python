"""A real OneShotInvoker against the REST peer, its adapter and link journaled."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from band.runtime.oneshot import OneShotInvoker
from tests.runtime.helpers import rest_client_over
from tests.scenarios.adapter import JournaledLink, ScriptedAdapter
from tests.scenarios.observations import Observations
from tests.scenarios.peer import AGENT_ID, BandPeer, room_json


def envelope(event_type: str, room_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The bridge envelope a one-shot host forwards for one platform event."""
    return {
        "event_type": event_type,
        "agent_id": AGENT_ID,
        "room_id": room_id,
        "payload": payload,
    }


@dataclass
class OneShotRig:
    observations: Observations
    peer: BandPeer
    adapter: ScriptedAdapter
    invoker: OneShotInvoker

    def deliver(self, room_id: str, text: str) -> asyncio.Task[dict[str, Any]]:
        """A user posts ``text`` and the host forwards it to the invoker."""
        message = self.peer.post(room_id, text)
        return asyncio.create_task(
            self.invoker.handle_event(envelope("message_created", room_id, message))
        )

    async def remove_room(self, room_id: str) -> dict[str, Any]:
        return await self.invoker.handle_event(
            envelope("room_removed", room_id, room_json(room_id))
        )

    @property
    def journal(self) -> list[str]:
        return self.observations.journal


@asynccontextmanager
async def oneshot_rig() -> AsyncIterator[OneShotRig]:
    observations = Observations()
    peer = BandPeer(observations)
    adapter = ScriptedAdapter(observations)
    async with rest_client_over(peer.answer) as rest:
        link = JournaledLink(
            observations,
            agent_id=AGENT_ID,
            api_key="test-key",
            rest_url="https://test.invalid",
        )
        link.rest = rest
        invoker = OneShotInvoker(link=link, adapter=adapter, agent_id=AGENT_ID)
        await invoker.startup()
        try:
            yield OneShotRig(observations, peer, adapter, invoker)
        finally:
            adapter.release_everything()
            await invoker.shutdown()
