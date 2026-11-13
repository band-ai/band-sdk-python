"""Run a provisioned agent that only handles contact events.

The contact smokes need a real ``Agent`` whose runtime subscribes to
``agent_contacts:<id>`` with the ``CALLBACK`` strategy, but no LLM: contact
events never reach the adapter. :class:`ContactInbox` is the callback, and it
lets a test await each event type the platform pushes.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Any, TypeVar

from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import PlatformMessage
from band.platform.event import ContactEvent
from band.runtime.contact_tools import ContactTools
from band.runtime.contacts import ContactEventConfig, ContactEventStrategy
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import (
    ProvisionedAgent,
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

    async def on_turn(
        self,
        messages: Sequence[PlatformMessage],
        tools: AgentToolsProtocol,
        history: Any,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        raise RuntimeError(f"contact listener was handed {len(messages)} message(s)")


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

    async def wait_for(self, kind: type[EventT], *, deadline_s: float) -> EventT:
        """Return the first delivered event of ``kind``; raise on the deadline."""
        async with asyncio.timeout(deadline_s), self._arrived:
            await self._arrived.wait_for(lambda: self._first(kind) is not None)
        event = self._first(kind)
        assert event is not None
        return event

    def _first(self, kind: type[EventT]) -> EventT | None:
        return next((e for e in self._events if isinstance(e, kind)), None)


@asynccontextmanager
async def running_contact_listener(
    agent: ProvisionedAgent, inbox: ContactInbox, settings: BaselineSettings
) -> AsyncGenerator[None, None]:
    """Run ``agent`` with ``inbox`` as its ``CALLBACK`` contact handler.

    The runtime joins ``agent_contacts:<id>`` before startup returns, so a
    request sent inside the block is delivered.
    """
    config = ContactEventConfig(
        strategy=ContactEventStrategy.CALLBACK, on_event=inbox.on_event
    )
    async with running_agent_with_handle(
        agent, TurnlessAdapter(), settings, contact_config=config
    ):
        yield
