"""Captured agent reply messages, with fluent tolerant assertions.

``Replies`` is the buffer a ``ReplyCapture`` fills from the room's
``message_created`` events (see ``capture.py``); it is a ``list`` of reply
messages with assertion methods attached, so the check lives with the data it
inspects.

The assertions are deliberately tolerant — they assert *behaviour held*, not
exact transcripts. There are no exact-count checks, literal-token recitation,
mandatory-silence, or strict-ordering assertions; agents are non-deterministic
and those make suites brittle.

- ``assert_present`` — at least one reply happened.
- ``assert_at_least`` — threshold-of-N replies (a floor, never an exact count).
- ``assert_contains_any`` — some reply contains any of the options (substring,
  case-insensitive); any-of, not all-of, so paraphrasing doesn't break it.
- ``assert_mentions`` — some reply mentions a participant, checked via message
  metadata (not by parsing ``@`` text).

A list subclass, so it iterates/indexes/``len``s like one. A derived subset
(slice or filter) is a plain ``list``, so re-wrap it to keep the methods:
``Replies(m for m in capture.messages if ...).assert_present()``.
"""

from __future__ import annotations

from typing import ClassVar

from band.client.streaming import MessageCreatedPayload

from band.core.types import MessageType

from tests.e2e.baseline.toolkit.observations.assertions import ContentAssertions


class Replies(ContentAssertions, list[MessageCreatedPayload]):
    """A list of captured agent reply messages, with tolerant assertions.

    ``assert_at_least`` and ``assert_contains_any`` come from
    :class:`ContentAssertions` (shared with ``Events``); the rest are reply-specific.
    Replies are text messages, so ``MESSAGE_TYPE`` is ``MessageType.TEXT``.
    """

    MESSAGE_TYPE: ClassVar[MessageType | None] = MessageType.TEXT

    def assert_present(self, *, what: str = "an agent reply") -> None:
        assert self, f"expected {what}, but no agent messages were captured"

    def assert_mentions(self, participant_id: str) -> None:
        for message in self:
            if message.metadata and any(
                mention.id == participant_id for mention in message.metadata.mentions
            ):
                return
        raise AssertionError(
            f"expected a reply mentioning {participant_id} (by metadata), but none did"
        )
