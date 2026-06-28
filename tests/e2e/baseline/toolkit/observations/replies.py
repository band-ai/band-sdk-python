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

from collections.abc import Iterable

from band.client.streaming import MessageCreatedPayload


class Replies(list[MessageCreatedPayload]):
    """A list of captured agent reply messages, with tolerant assertions."""

    def assert_present(self, *, what: str = "an agent reply") -> None:
        assert self, f"expected {what}, but no agent messages were captured"

    def assert_at_least(self, n: int) -> None:
        assert len(self) >= n, (
            f"expected at least {n} agent reply/replies, got {len(self)}"
        )

    def assert_contains_any(self, options: Iterable[str]) -> None:
        options = list(options)
        haystack = "\n".join(m.content for m in self).lower()
        assert any(option.lower() in haystack for option in options), (
            f"expected a reply containing any of {options}, but none did:\n{haystack}"
        )

    def assert_mentions(self, participant_id: str) -> None:
        for message in self:
            if message.metadata and any(
                mention.id == participant_id for mention in message.metadata.mentions
            ):
                return
        raise AssertionError(
            f"expected a reply mentioning {participant_id} (by metadata), but none did"
        )
