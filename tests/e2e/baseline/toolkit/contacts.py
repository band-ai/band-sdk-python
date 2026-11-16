"""Run two provisioned agents that exchange contact events.

The contact smokes need a real ``Agent`` whose runtime subscribes to
``agent_contacts:<id>`` with the ``CALLBACK`` strategy, but no LLM: contact
events never reach the adapter. :class:`ContactInbox` is the callback, and it
lets a test await each event type the platform pushes. The requester is a
second agent because the platform refuses a user's request to their own agent.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, TypeVar

from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import PlatformMessage
from band.platform.event import ContactEvent
from band.runtime.contact_tools import ContactTools
from band.runtime.contacts import ContactEventConfig, ContactEventStrategy
from tests.e2e.baseline.toolkit.provisioning import (
    PeerActor,
    ProvisionedAgent,
    ResourceManager,
    running_agent_with_handle,
)

EventT = TypeVar("EventT", bound=ContactEvent)


class TurnlessAdapter(SimpleAdapter[Any]):
    """An adapter for an agent that is never mentioned, so any turn is a test bug."""

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
        raise RuntimeError(f"contact listener was handed message {msg.id}")


class ContactInbox:
    """The ``CALLBACK`` handler: records each contact event and the tools it got."""

    def __init__(self) -> None:
        self._events: list[ContactEvent] = []
        self._tools: ContactTools | None = None
        self._arrived = asyncio.Condition()

    async def on_event(self, event: ContactEvent, tools: ContactTools) -> None:
        async with self._arrived:
            self._events.append(event)
            self._tools = tools
            self._arrived.notify_all()

    @property
    def tools(self) -> ContactTools:
        """The ``ContactTools`` the runtime handed the callback."""
        if self._tools is None:
            raise RuntimeError("no contact event has been delivered yet")
        return self._tools

    async def wait_for(self, kind: type[EventT]) -> EventT:
        """Return the first delivered event of ``kind``, under the caller's deadline."""
        async with self._arrived:
            return await self._arrived.wait_for(lambda: self._first(kind))

    def _first(self, kind: type[EventT]) -> EventT | None:
        return next((e for e in self._events if isinstance(e, kind)), None)


@dataclass(frozen=True)
class ContactParties:
    """A target agent handling contact events, and a peer that can request it."""

    target_handle: str
    requester: ProvisionedAgent
    requester_handle: str
    requester_actor: PeerActor
    inbox: ContactInbox


@asynccontextmanager
async def running_contact_parties(
    resources: ResourceManager,
) -> AsyncGenerator[ContactParties, None]:
    """Provision a target and a requester; run the target with a ``ContactInbox``.

    The runtime joins ``agent_contacts:<id>`` before startup returns, so a
    request sent inside the block is delivered. Reaping both agents at teardown
    deletes any contact relationship the block created.
    """
    target = await resources.provision_agent("contacttarget")
    requester = await resources.provision_agent("contactrequester")
    requester_actor = resources.peer(requester)
    inbox = ContactInbox()
    config = ContactEventConfig(
        strategy=ContactEventStrategy.CALLBACK, on_event=inbox.on_event
    )
    async with running_agent_with_handle(
        target, TurnlessAdapter(), resources.settings, contact_config=config
    ):
        yield ContactParties(
            target_handle=await resources.peer(target).handle(),
            requester=requester,
            requester_handle=await requester_actor.handle(),
            requester_actor=requester_actor,
            inbox=inbox,
        )
