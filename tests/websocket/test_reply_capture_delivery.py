"""Unit tests for the delivery-status processing barrier.

Covers ``ReplyCapture``'s consumption of ``message_updated`` delivery state and
the ``wait_for_processed`` barrier — pure async logic, no live platform, so it
runs in normal CI (unlike the live ``test_processing_barrier`` smoke). The
behaviour encoded here is grounded in the backend lifecycle
``delivered -> processing -> processed | failed`` where ``failed`` is retryable
and only ``processed`` is a success terminal.
"""

from __future__ import annotations

import asyncio

import pytest

from band.client.streaming import DeliveryStatus, MessageCreatedPayload
from tests.e2e.baseline.toolkit.waiting import ReplyCapture

MSG = "msg-1"
AGENT = "agent-1"


def _delivery_update(status: str, *, error: str | None = None) -> MessageCreatedPayload:
    """A message_updated payload carrying one recipient's delivery state."""
    attempt: dict = {"attempt_number": 1, "status": status}
    if error is not None:
        attempt["error"] = error
    return MessageCreatedPayload(
        id=MSG,
        content="(barrier message)",
        message_type="text",
        metadata={
            "delivery_status": {
                AGENT: {"status": status, "current_attempt": 1, "attempts": [attempt]}
            }
        },
        sender_id="user-1",
        sender_type="User",
        inserted_at="t",
        updated_at="t",
    )


def test_delivery_status_accessor_parses_to_enum() -> None:
    capture = ReplyCapture("room-1")
    assert capture.delivery_status(MSG, AGENT) is None  # nothing seen yet

    capture._on_message_updated(_delivery_update("processing"))
    assert capture.delivery_status(MSG, AGENT) is DeliveryStatus.PROCESSING

    capture._on_message_updated(_delivery_update("processed"))
    assert capture.delivery_status(MSG, AGENT) is DeliveryStatus.PROCESSED


def test_delivery_status_unknown_value_is_none() -> None:
    capture = ReplyCapture("room-1")
    capture._on_message_updated(_delivery_update("teleported"))  # not a real status
    assert capture.delivery_status(MSG, AGENT) is None


def test_delivery_history_records_deduplicated_transitions() -> None:
    """The full backend lifecycle, deduplicating repeated states."""
    capture = ReplyCapture("room-1")
    for status in ["delivered", "processing", "processing", "processed"]:
        capture._on_message_updated(_delivery_update(status))

    assert capture.delivery_history(MSG, AGENT) == [
        DeliveryStatus.DELIVERED,
        DeliveryStatus.PROCESSING,  # the duplicate is collapsed
        DeliveryStatus.PROCESSED,
    ]


async def test_wait_for_delivery_resolves_on_each_target_state() -> None:
    # Every non-terminal state is individually awaitable via the general waiter.
    for status, expected in [
        ("delivered", DeliveryStatus.DELIVERED),
        ("processing", DeliveryStatus.PROCESSING),
        ("failed", DeliveryStatus.FAILED),
    ]:
        capture = ReplyCapture("room-1", deadline_s=2)
        waiter = asyncio.create_task(
            capture.wait_for_delivery(MSG, AGENT, until={expected})
        )
        await asyncio.sleep(0)
        capture._on_message_updated(_delivery_update(status))
        reached = await asyncio.wait_for(waiter, timeout=1)
        assert reached is expected


async def test_wait_for_delivery_accepts_any_of_several_targets() -> None:
    capture = ReplyCapture("room-1", deadline_s=2)
    waiter = asyncio.create_task(
        capture.wait_for_delivery(
            MSG, AGENT, until={DeliveryStatus.PROCESSED, DeliveryStatus.FAILED}
        )
    )
    await asyncio.sleep(0)
    capture._on_message_updated(_delivery_update("failed", error="x"))
    reached = await asyncio.wait_for(waiter, timeout=1)
    assert reached is DeliveryStatus.FAILED


async def test_wait_for_processed_resolves_on_processed() -> None:
    capture = ReplyCapture("room-1", deadline_s=2)
    waiter = asyncio.create_task(capture.wait_for_processed(MSG, AGENT))
    await asyncio.sleep(0)  # let the waiter start and block

    capture._on_message_updated(_delivery_update("processed"))
    await asyncio.wait_for(waiter, timeout=1)  # returns without raising


async def test_wait_for_processed_waits_through_failed_then_succeeds() -> None:
    """FAILED is transient (the platform retries), so the barrier must not give
    up on it — it resolves only once PROCESSED arrives."""
    capture = ReplyCapture("room-1", deadline_s=2)
    waiter = asyncio.create_task(capture.wait_for_processed(MSG, AGENT))
    await asyncio.sleep(0)

    capture._on_message_updated(_delivery_update("failed", error="boom"))
    await asyncio.sleep(0.05)
    assert not waiter.done()  # did NOT short-circuit on failed

    capture._on_message_updated(_delivery_update("processing"))
    capture._on_message_updated(_delivery_update("processed"))
    await asyncio.wait_for(waiter, timeout=1)


async def test_wait_for_processed_timeout_reports_last_status_and_error() -> None:
    capture = ReplyCapture("room-1")
    capture._on_message_updated(_delivery_update("failed", error="kaboom"))

    with pytest.raises(TimeoutError) as excinfo:
        await capture.wait_for_processed(MSG, AGENT, deadline_s=0.1)

    message = str(excinfo.value)
    assert "failed" in message  # last status surfaced
    assert "kaboom" in message  # attempt error surfaced
