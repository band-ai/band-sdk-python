"""Band public adapter extension for independently addressed PAP participants."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import Field
from typing_extensions import Unpack

from band.core.adapterconfig import BaseAdapterConfig
from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import FeatureKwargs, PlatformMessage, metadata_to_dict
from bandpap.routing import Grants
from bandpap.storage import StateUnavailable
from bandpap.wire import Message, SendMessage
from bandpap.worker import ConversationWorker, stable_id


class PAPAdapterConfig(BaseAdapterConfig):
    proxy_id: str = Field(min_length=1)


class PAPAdapter(SimpleAdapter[Any]):
    def __init__(
        self,
        config: PAPAdapterConfig,
        *,
        grants: Grants,
        worker: ConversationWorker,
        history_converter: Any = None,
        **features: Unpack[FeatureKwargs],
    ) -> None:
        super().__init__(history_converter=history_converter, **features)
        if config.proxy_id != worker.proxy or grants is not worker.grants:
            raise ValueError("Adapter and worker must share a proxy and grant registry")
        self.config, self.grants, self.worker = config, grants, worker
        self.task: asyncio.Task[None] | None = None

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        await super().on_started(agent_name, agent_description)
        if self.require_platform().agent_id != self.config.proxy_id:
            raise ValueError("Each PAP participant requires its own Band credential")
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.worker.run())

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
        # Default history drops original IDs/addresses; only durable accepted input is replayed.
        if msg.room_id != room_id or msg.message_type != "text":
            tools.turn.settle()
            return
        try:
            grant = self.grants.addressed(
                room=room_id,
                proxy=self.config.proxy_id,
                source=msg.sender_id,
                sender_type=msg.sender_type,
                metadata=metadata_to_dict(msg.metadata),
                message_id=stable_id(msg.id),
            )
        except StateUnavailable:
            tools.turn.settle()
            return
        message_id = stable_id(msg.id)
        request = self.grants.command(grant.id, message_id)
        sender = "human" if grant.sender_type == "User" else "agent"
        if request is None:
            request = SendMessage(
                message=Message(id=message_id, sender=sender, text=msg.content)
            )
        elif request.message.sender != sender:
            raise StateUnavailable(
                "Structured sender disagrees with authenticated source"
            )
        session = await self.worker.current_session(grant)
        self.grants.store.enqueue(session, grant.binding, request)
        tools.turn.settle()

    async def on_cleanup(self, room_id: str) -> None:
        self.grants.pause_room(self.config.proxy_id, room_id)

    async def on_interrupt(self, room_id: str, mode: Any) -> None:
        self.grants.pause_room(self.config.proxy_id, room_id)

    async def cleanup_all(self) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
