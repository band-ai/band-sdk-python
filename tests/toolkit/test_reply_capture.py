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

from band.client.streaming import DeliveryStatus, MessageCreatedPayload

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


async def test_processed_reached_but_reply_not_yet_buffered_is_the_race() -> None:
    """The race state, and why ``wait_for_processed`` can't be the reply barrier: the
    delivery status reaches PROCESSED (so ``wait_for_processed`` returns) while the reply
    frame is not yet buffered — both true at once. A test that read ``capture.messages``
    here would see nothing. It also pins the reply-optional contract: ``wait_for_processed``
    must return on the PROCESSED signal alone (feeding no reply, it would hang otherwise)."""
    capture = ReplyCapture(ROOM)
    trigger = "m-trigger"

    # Racey backend order: delivery-status PROCESSED arrives, reply frame has not.
    capture._on_message_updated(_processed(trigger, AGENT))
    await capture.wait_for_processed(trigger, AGENT)

    # PROCESSED is genuinely reached...
    assert capture.delivery_status(trigger, AGENT) == DeliveryStatus.PROCESSED
    # ...yet no reply is buffered: reading capture.messages here would race the reply.
    assert not capture.messages


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


async def test_wait_for_reply_scopes_past_the_since_cursor() -> None:
    """With ``since``, a reply from an *earlier* turn (before the cursor) must not
    satisfy the wait — only a reply captured after the cursor counts. This guards the
    reused-capture pattern (``mark = messages.snapshot()`` then ``since=mark``) that the
    multi-turn matrix tests rely on to scope each turn."""
    capture = ReplyCapture(ROOM)

    # An earlier turn's reply is already in the buffer; the cursor sits after it.
    capture._on_message(_reply(AGENT, "earlier-turn reply", mid="m-old"))
    mark = capture.messages.snapshot()

    trigger = "m-trigger"
    waiter = asyncio.ensure_future(
        capture.wait_for_reply(trigger, AGENT, since=mark, deadline_s=5)
    )
    capture._on_message_updated(_processed(trigger, AGENT))
    await _drain()
    assert not waiter.done(), "a reply before the cursor must not satisfy the wait"

    capture._on_message(_reply(AGENT, "this-turn reply", mid="m-new"))
    replies = await asyncio.wait_for(waiter, timeout=1)

    assert [r.content for r in replies] == ["this-turn reply"]


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
