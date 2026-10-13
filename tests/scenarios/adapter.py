"""A real SimpleAdapter whose turns and cleanups are journaled and held on cue."""

from __future__ import annotations

import asyncio
from typing import Any

from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import PlatformMessage
from band.platform.link import BandLink
from tests.scenarios.observations import Observations


class ScriptedAdapter(SimpleAdapter):
    """Declines every turn after an optional hold, so turns settle cleanly."""

    def __init__(self, observations: Observations) -> None:
        super().__init__()
        self.observations = observations
        self._held: dict[str, asyncio.Event] = {}

    def hold(self, text: str) -> None:
        self._held[text] = asyncio.Event()

    def release(self, text: str) -> None:
        self._held.pop(text).set()

    def release_everything(self) -> None:
        for held in self._held.values():
            held.set()
        self._held.clear()

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: Any,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        self.observations.record(f"{room_id}: turn {msg.content}")
        if (held := self._held.get(msg.content)) is not None:
            await held.wait()
        await tools.no_reply("scenario turn")
        self.observations.record(f"{room_id}: turn {msg.content} done")

    async def on_cleanup(self, room_id: str) -> None:
        self.observations.record(f"{room_id}: room cleanup")

    async def cleanup_all(self) -> None:
        self.observations.record("cleanup_all")


class JournaledLink(BandLink):
    """A real link that journals its disconnect."""

    def __init__(self, observations: Observations, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.observations = observations

    async def disconnect(self) -> None:
        await super().disconnect()
        self.observations.record("disconnect")
