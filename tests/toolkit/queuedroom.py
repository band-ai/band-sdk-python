"""A local queued room whose opener's observer status arrives after its HTTP settlement."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, cast

import httpx

from band.client.streaming import DeliveryStatus, MessageCreatedPayload
from band.core.protocols import AgentToolsProtocol
from band.runtime.messages import PlatformMessage
from band.testing.fake_tools import FakeAgentTools
from tests.e2e.baseline.smoke.samples.queuedroom import QueuedRoom, Sender
from tests.e2e.baseline.timeouts import SlowTurnBudget
from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.invocations import InvocationObserver
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.framework_conformance.invocationkit import (
    ROOM_ID,
    DecliningAdapter,
    original,
    turn_input,
)
from tests.runtime.helpers import AGENT_ID, LifecyclePlatform, page, rest_client_over

AGENT = ProvisionedAgent(id=AGENT_ID, api_key="test-key", name="Agent")
DEADLINE_S = 5.0


class EchoAdapter(DecliningAdapter):
    async def on_turn(
        self,
        messages: Sequence[PlatformMessage],
        tools: AgentToolsProtocol,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await tools.send_message(
            messages[-1].content, mentions=[messages[-1].sender_id]
        )


class QueuedPeer:
    def __init__(self) -> None:
        self.platform = LifecyclePlatform()
        self.pending: asyncio.Queue[PlatformMessage] = asyncio.Queue()
        self.adapter = EchoAdapter()

    async def send_message(
        self, room_id: str, content: str, *, mention_id: str, mention_name: str
    ) -> str:
        message_id = f"input-{len(self.platform.messages)}"
        message = replace(original(message_id), content=content)
        row = self.platform.add_message(message_id, content=content)
        row.update(chat_room_id=ROOM_ID, sender_id=message.sender_id)
        self.pending.put_nowait(message)
        return message_id

    def answer(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.path.endswith("/messages")
        return page(list(reversed(self.platform.messages)))

    def status(
        self, message: PlatformMessage, status: DeliveryStatus
    ) -> MessageCreatedPayload:
        row = next(row for row in self.platform.messages if row["id"] == message.id)
        row["metadata"] = {"delivery_status": {AGENT.id: {"status": status}}}
        return MessageCreatedPayload.model_validate(row)

    async def turn(
        self,
        capture: ReplyCapture,
        messages: tuple[PlatformMessage, ...],
        *,
        opener: bool = False,
    ) -> None:
        for message in messages:
            self.status(message, DeliveryStatus.PROCESSING)
        inp = turn_input(*messages)
        if opener:
            await self.adapter.on_event(inp)
        else:
            await self.adapter.on_messages(inp)
        for post in cast(FakeAgentTools, inp.tools).messages_sent:
            row = self.platform.add_message(
                f"reply-{len(self.platform.messages)}", content=post["content"]
            )
            row.update(chat_room_id=ROOM_ID, sender_id=AGENT.id, sender_type="Agent")
            capture._on_message(MessageCreatedPayload.model_validate(row))
        for message in messages:
            update = self.status(message, DeliveryStatus.PROCESSED)
            if not opener:
                capture._on_message_updated(update)

    async def run(self, capture: ReplyCapture) -> None:
        opener = await self.pending.get()
        await self.turn(capture, (opener,), opener=True)
        queued: list[PlatformMessage] = []
        while not self.pending.empty():
            queued.append(self.pending.get_nowait())
        assert queued, "the held opener must have queued inputs behind it"
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self.turn(capture, tuple(queued)))
            # HTTP settlement frees the room before its observer frame arrives.
            asyncio.get_running_loop().call_soon(
                capture._on_message_updated,
                self.status(opener, DeliveryStatus.PROCESSED),
            )


@asynccontextmanager
async def queued_room() -> AsyncIterator[QueuedRoom]:
    observer = InvocationObserver(deadline_s=DEADLINE_S)
    peer = QueuedPeer()
    observer.watch(peer.adapter)
    async with rest_client_over(peer.answer) as rest:
        capture = ReplyCapture(ROOM_ID, user_ops=UserOps(rest), deadline_s=DEADLINE_S)
        async with asyncio.TaskGroup() as tasks:
            driver = tasks.create_task(peer.run(capture))
            try:
                yield QueuedRoom(
                    agent=AGENT,
                    room_id=ROOM_ID,
                    capture=capture,
                    observer=observer,
                    senders=[Sender(post=peer, id="alice")],
                    budget=SlowTurnBudget(deadline_s=DEADLINE_S, extra_s=0),
                )
            finally:
                observer.release_all()
                driver.cancel()
