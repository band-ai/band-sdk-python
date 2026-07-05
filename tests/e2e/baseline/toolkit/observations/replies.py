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

    def assert_at_most(self, n: int) -> None:
        """Assert *at most* ``n`` replies — the one upper-bound check, for runaway guards.

        The deliberate exception to the toolkit's floors-only rule, so it lives here on
        ``Replies`` and **not** on the shared :class:`ContentAssertions` floor mixin
        (which ``Events`` shares) — an upper bound there would erode the "a floor, never a
        ceiling" contract for every collection. Use it **only** after scoping to a sender
        and a post-trigger window (``from_sender(...).since(...)``), with a deliberately
        high ceiling that a normal one-turn reply batch never crosses but a self-re-dispatch
        loop does. It is a runaway guard, **not** an exact "one reply" or model-driven
        reply-count assertion; an infinite loop is caught by the barrier timeout, not here.
        """
        count = len(self)
        if count > n:
            haystack = "\n".join(message.content for message in self)
            raise AssertionError(
                f"expected at most {n} {self._noun()}(s), got {count} — a self-dispatch "
                f"loop, not model reply batching:\n{haystack}"
            )

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

    def from_sender(self, sender_id: str) -> "Replies":
        """The subset of replies authored by ``sender_id``.

        Scope an assertion to one participant's own messages when several agents
        post into the same captured room — e.g. a peer and the agent under test,
        where the peer's own message would otherwise satisfy a content assertion.
        Re-wraps the filter so the tolerant assertion methods survive.
        """
        return Replies(message for message in self if message.sender_id == sender_id)

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
