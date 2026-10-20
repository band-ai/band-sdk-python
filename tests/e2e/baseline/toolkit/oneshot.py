"""Drive a OneShotInvoker with actual platform frames from a user observer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from band.client.streaming import MessageCreatedPayload
from band.client.streaming.client import WireEvent
from band.core.simple_adapter import SimpleAdapter
from band.platform.link import BandLink
from band.runtime.oneshot import OneShotInvoker
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent
from tests.e2e.baseline.toolkit.ws import user_ws_observer


@asynccontextmanager
async def running_invoker_with_handle(
    identity: ProvisionedAgent,
    adapter: SimpleAdapter[Any],
    settings: BaselineSettings,
    *,
    room_id: str,
) -> AsyncIterator[OneShotInvoker]:
    """Forward real message frames; close ingress before draining the invoker."""
    link = BandLink(
        agent_id=identity.id,
        api_key=identity.api_key,
        rest_url=settings.endpoints.rest_url,
        ws_url=settings.endpoints.ws_url,
    )
    invoker = OneShotInvoker(link=link, adapter=adapter, agent_id=identity.id)

    async def forward(payload: MessageCreatedPayload) -> None:
        if payload.sender_type != "User":
            return
        await invoker.handle_event(
            {
                "event_type": WireEvent.MESSAGE_CREATED,
                "room_id": room_id,
                "payload": payload.model_dump(mode="json"),
            }
        )

    try:
        await invoker.startup()
        async with user_ws_observer(settings) as observer:
            await observer.join_chat_room_channel(room_id, forward)
            try:
                yield invoker
            finally:
                await observer.leave_chat_room_channel(room_id)
                await invoker.shutdown()
    finally:
        await invoker.shutdown()
