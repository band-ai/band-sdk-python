"""Tolerant assertions over captured agent replies.

Deliberately tolerant — they assert *behaviour held*, not exact transcripts.
There are no exact-count checks, literal-token recitation, mandatory-silence,
or strict-ordering assertions; agents are non-deterministic and those make
suites brittle. Each takes the list of captured agent messages (see
``ReplyCapture.messages``) and raises ``AssertionError`` with a readable reason.

- ``assert_present`` — at least one reply happened.
- ``assert_at_least`` — threshold-of-N replies (a floor, never an exact count).
- ``assert_contains_any`` — some reply contains any of the options (substring,
  case-insensitive); any-of, not all-of, so paraphrasing doesn't break it.
- ``assert_mentions`` — some reply mentions a participant, checked via message
  metadata (not by parsing ``@`` text).

Asserting which *tool* fired pairs with trajectory/tool-observation inspection,
which needs an event capture beyond message_created; it lives with that tool.
"""

from __future__ import annotations

from collections.abc import Iterable

from band.client.streaming import MessageCreatedPayload


def assert_present(
    messages: list[MessageCreatedPayload], *, what: str = "an agent reply"
) -> None:
    assert messages, f"expected {what}, but no agent messages were captured"


def assert_at_least(messages: list[MessageCreatedPayload], n: int) -> None:
    assert len(messages) >= n, (
        f"expected at least {n} agent reply/replies, got {len(messages)}"
    )


def assert_contains_any(
    messages: list[MessageCreatedPayload], options: Iterable[str]
) -> None:
    options = list(options)
    haystack = "\n".join(m.content for m in messages).lower()
    assert any(option.lower() in haystack for option in options), (
        f"expected a reply containing any of {options}, but none did:\n{haystack}"
    )


def assert_mentions(messages: list[MessageCreatedPayload], participant_id: str) -> None:
    for message in messages:
        if message.metadata and any(
            mention.id == participant_id for mention in message.metadata.mentions
        ):
            return
    raise AssertionError(
        f"expected a reply mentioning {participant_id} (by metadata), but none did"
    )
