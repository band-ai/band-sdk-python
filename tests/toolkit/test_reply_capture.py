"""Deterministic guard for the baseline E2E toolkit's reply barrier.

The live E2E suites drive :class:`ReplyCapture` against the platform, where the
reply's ``message_created`` frame and the delivery-status ``message_updated``
frame are *independent, unordered* backend events. When PROCESSED arrives a beat
before the reply frame, a test that reads ``capture.messages`` the instant
``wait_for_processed`` returns sees an empty buffer — the cross-adapter
"no agent messages were captured" flake.

That ordering is rare and timing-dependent live, so it can't be reproduced on
demand there. Here we drive the capture's event handlers directly, which lets us
*force* the racey order and prove:

* reading the buffer right after ``wait_for_processed`` races the reply
  (documents why that pattern is unsafe), and
* ``wait_for_reply`` waits for the reply frame regardless of arrival order.

This runs in the fast unit lane (no live platform), unlike the ``tests/e2e``
tree which is skipped unless ``E2E_TESTS_ENABLED``.
"""

from __future__ import annotations

import asyncio

import pytest

from band.client.streaming import MessageCreatedPayload

from tests.e2e.baseline.toolkit.capture import ReplyCapture

ROOM = "room-x"
AGENT = "agent-1"
PEER = "peer-2"


def _reply(
    sender_id: str, content: str, *, mid: str = "m-reply"
) -> MessageCreatedPayload:
    """A captured agent text reply (what ``message_created`` delivers)."""
    return MessageCreatedPayload(
        id=mid,
        content=content,
        message_type="text",
        sender_id=sender_id,
        sender_type="Agent",
        inserted_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def _processed(message_id: str, recipient_id: str) -> MessageCreatedPayload:
    """A delivery-status frame marking ``message_id`` PROCESSED for ``recipient_id``
    (what ``message_updated`` delivers)."""
    return MessageCreatedPayload(
        id=message_id,
        content="",
        message_type="text",
        sender_id="user-0",
        sender_type="User",
        inserted_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        metadata={"delivery_status": {recipient_id: {"status": "processed"}}},
    )


async def _drain() -> None:
    """Let the waiter task run its predicate check between fed events."""
    for _ in range(3):
        await asyncio.sleep(0)


async def test_processed_before_reply_leaves_buffer_empty() -> None:
    """The unsafe pattern: PROCESSED can land before the reply frame, so reading
    ``messages`` right after ``wait_for_processed`` returns can see nothing."""
    capture = ReplyCapture(ROOM)
    trigger = "m-trigger"

    # Racey backend order: delivery-status PROCESSED arrives first.
    capture._on_message_updated(_processed(trigger, AGENT))

    await capture.wait_for_processed(trigger, AGENT)
    # The turn is "done" by the barrier, yet the reply frame hasn't been delivered.
    assert not capture.messages, (
        "the processed barrier does not imply the reply is buffered"
    )


async def test_wait_for_reply_awaits_a_late_reply_frame() -> None:
    """``wait_for_reply`` blocks past PROCESSED until the reply frame is captured."""
    capture = ReplyCapture(ROOM)
    trigger = "m-trigger"

    waiter = asyncio.ensure_future(capture.wait_for_reply(trigger, AGENT, deadline_s=5))

    # PROCESSED first — the racey order that fools wait_for_processed.
    capture._on_message_updated(_processed(trigger, AGENT))
    await _drain()
    assert not waiter.done(), "must keep waiting: processed seen but no reply yet"

    # The reply frame arrives late; the barrier now releases with it.
    capture._on_message(_reply(AGENT, "hello there"))
    replies = await asyncio.wait_for(waiter, timeout=1)

    assert [r.content for r in replies] == ["hello there"]


async def test_wait_for_reply_scopes_to_the_recipient() -> None:
    """The barrier scopes to the recipient (the agent that processed the trigger) — a
    peer's own captured message must not satisfy it, even though the buffer holds it.
    The wait keys on the recipient's PROCESSED signal, so it waits for the recipient's
    reply, which is what makes it safe in a multi-agent room."""
    capture = ReplyCapture(ROOM)
    trigger = "m-trigger"

    waiter = asyncio.ensure_future(capture.wait_for_reply(trigger, AGENT, deadline_s=5))

    capture._on_message_updated(_processed(trigger, AGENT))
    capture._on_message(_reply(PEER, "peer chatter", mid="m-peer"))
    await _drain()
    assert not waiter.done(), (
        "a peer's reply must not satisfy the recipient-scoped wait"
    )

    capture._on_message(_reply(AGENT, "the agent's answer", mid="m-agent"))
    replies = await asyncio.wait_for(waiter, timeout=1)

    assert [r.sender_id for r in replies] == [AGENT]


async def test_wait_for_reply_times_out_on_a_silent_turn() -> None:
    """Processed but no reply within the deadline raises a diagnostic TimeoutError
    that names the silent turn (not a bare empty buffer)."""
    capture = ReplyCapture(ROOM)
    trigger = "m-trigger"

    capture._on_message_updated(_processed(trigger, AGENT))

    with pytest.raises(
        TimeoutError, match="processed message m-trigger.*captured no reply"
    ):
        await capture.wait_for_reply(trigger, AGENT, deadline_s=0.2)
