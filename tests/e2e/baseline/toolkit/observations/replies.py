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
- ``mentioning`` — the subset of replies that mention a participant (by metadata),
  for chaining into a further assertion (e.g. content) on just those replies.

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

    def mentioning(self, participant_id: str) -> "Replies":
        """The subset of replies that mention ``participant_id`` (by metadata).

        Re-wraps the filter so the tolerant assertion methods survive (a bare
        filter over a ``list`` subclass is a plain ``list``).
        """
        return Replies(
            message
            for message in self
            if message.metadata is not None
            and any(
                mention.id == participant_id for mention in message.metadata.mentions
            )
        )

    def assert_mentions(self, participant_id: str) -> None:
        if not self.mentioning(participant_id):
            raise AssertionError(
                f"expected a reply mentioning {participant_id} (by metadata), but none did"
            )

    def snapshot(self) -> int:
        """A cursor at the current end of the buffer, for ``since`` after a turn.

        ``mark = capture.messages.snapshot()`` before sending, then
        ``capture.messages.since(mark)`` reads only what arrived afterwards — no
        manual ``len(...)`` / slice index.
        """
        return len(self)

    def since(self, cursor: int) -> "Replies":
        """The replies captured after ``cursor`` (from ``snapshot``), as a ``Replies``.

        Re-wraps the slice so the tolerant assertion methods survive (a bare slice
        of a ``list`` subclass is a plain ``list``).
        """
        return Replies(self[cursor:])
