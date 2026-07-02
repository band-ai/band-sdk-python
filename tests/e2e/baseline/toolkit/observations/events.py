"""Emitted-event capture and assertions for live E2E tests.

The non-``tool_call`` event kinds an agent emits in a turn -- the free-text
``thought`` / ``error`` / ``task`` ``MessageType``s. They read back via the Human
messages API (``UserOps.list_messages``) on the same durable, race-free
"read after the barrier" path :class:`ToolCalls` uses (see ``tool_calls.py`` for
the contract), only filtered to a different ``message_type``.

Unlike ``tool_call`` (JSON ``{name, args}``), this content is **free text**, so
matching stays substring-based -- no JSON parsing. A shared :class:`Events` base
carries the read and the tolerant assertions; the thin subclasses
:class:`Thoughts` / :class:`Errors` / :class:`Tasks` just bind their
``MessageType`` and can grow bespoke assertions later.

Tests reach this through ``ReplyCapture.thoughts`` / ``errors`` / ``tasks`` (or
the generic ``ReplyCapture.events``).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import ClassVar

from band_rest import ChatMessage

from band.core.types import MessageType, is_usage_event

from tests.e2e.baseline.toolkit.observations.assertions import ContentAssertions
from tests.e2e.baseline.toolkit.user_ops import UserOps

logger = logging.getLogger(__name__)


class Events(ContentAssertions, list[ChatMessage]):
    """An agent's emitted events of one ``MessageType`` for a turn: a
    ``list[ChatMessage]`` with fluent, tolerant assertions.

    Subclasses bind a concrete type via ``MESSAGE_TYPE``; the base reads and
    asserts generically over the events' free-text ``content``. Being a list, it
    iterates, indexes, and ``len()``s like one. Read once (see ``Events.read`` /
    ``ReplyCapture.events``), then assert as many times as needed.

    ``assert_at_least`` and ``assert_contains_any`` come from
    :class:`ContentAssertions` (shared with ``Replies``).
    """

    MESSAGE_TYPE: ClassVar[MessageType | None] = None

    @classmethod
    async def read(
        cls,
        user_ops: UserOps,
        room_id: str,
        *,
        message_type: MessageType | None = None,
        sender_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> Events:
        """Read a room's events of one type, oldest-first.

        ``message_type`` defaults to the subclass's ``MESSAGE_TYPE``; pass it
        explicitly on the base ``Events`` (a ``ValueError`` is raised if neither
        is set). Pass ``sender_id`` to keep only one agent's events (rooms can
        hold several agents). Call after the turn is known complete (e.g. after
        ``wait_for_processed``); tests usually reach this via ``ReplyCapture``.

        Without ``since`` this returns every event of that type in the room (the
        turn only when the capture spans a single turn). Pass ``since`` (a server
        timestamp) to exclude earlier turns when reusing a capture.
        """
        mt = message_type or cls.MESSAGE_TYPE
        if mt is None:
            raise ValueError(
                "Events.read needs a message_type, or a subclass that binds one"
            )
        messages = await user_ops.list_messages(
            room_id, message_type=mt, since=since, limit=limit
        )
        # Keep only the requested sender's events, and drop usage records: they
        # ride task events (USAGE_EVENT_TYPE) but are not lifecycle tasks (they
        # have their own Usage observation). Only task events can carry usage, so
        # the is_usage_event filter is a no-op for thought/error reads.
        return cls(
            message
            for message in messages
            if (sender_id is None or message.sender_id == sender_id)
            and not is_usage_event(message.metadata)
        )

    def present(self) -> bool:
        """True if any event of this type was captured."""
        return len(self) > 0

    def assert_present(self, *, what: str | None = None) -> None:
        """Assert at least one event of this type was emitted.

        Named ``assert_present`` to match the sibling collections (``Replies``,
        ``Memories``); the failure message keeps the event-specific verb.
        """
        label = what or (
            f"a {self.MESSAGE_TYPE.value} event" if self.MESSAGE_TYPE else "an event"
        )
        if not self:
            raise AssertionError(f"expected {label}, but none were emitted")


class Thoughts(Events):
    """Captured ``thought`` events."""

    MESSAGE_TYPE: ClassVar[MessageType | None] = MessageType.THOUGHT


class Errors(Events):
    """Captured ``error`` events."""

    MESSAGE_TYPE: ClassVar[MessageType | None] = MessageType.ERROR


class Tasks(Events):
    """Captured ``task`` events."""

    MESSAGE_TYPE: ClassVar[MessageType | None] = MessageType.TASK
