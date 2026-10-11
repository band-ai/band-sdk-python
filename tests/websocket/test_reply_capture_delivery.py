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

import httpx
import pytest

from band.client.streaming import DeliveryStatus, MessageCreatedPayload
from tests.e2e.baseline.toolkit import capture as capture_module
from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.runtime.helpers import rest_client_over

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
    """FAILED is not terminal (a new agent context can run the message again),
    so the barrier must not give up on it — it resolves only once PROCESSED
    arrives."""
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


async def test_timeout_reports_durable_completion_without_passing_stale_observer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    content = "private message body"
    message = _delivery_update("processed").model_dump()
    message.update(content=content, inserted_at=None, updated_at=None)
    async with rest_client_over(
        lambda _: httpx.Response(
            200,
            json={"data": [message], "metadata": {"has_more": False, "limit": 100}},
        )
    ) as rest:
        capture = ReplyCapture("room-1", user_ops=UserOps(rest))
        capture._on_message_updated(_delivery_update("processing"))

        with pytest.raises(TimeoutError, match="last delivery status: processing"):
            await capture.wait_for_processed(MSG, AGENT, deadline_s=0.01)

    assert "websocket=processing history=['processing'] rest=processed" in caplog.text
    assert content not in caplog.text
    assert capture.delivery_status(MSG, AGENT) is DeliveryStatus.PROCESSING


@pytest.mark.parametrize("newer_type", ["tool_result", "text"])
async def test_timeout_snapshot_finds_note_beyond_first_message_page(
    newer_type: str, caplog: pytest.LogCaptureFixture
) -> None:
    note = _delivery_update("failed").model_dump()
    note.update(content="private note", inserted_at=None, updated_at=None)
    messages = [
        {**note, "id": f"other-{index}", "message_type": newer_type}
        for index in range(205)
    ]
    messages[105] = note
    cursors: list[str | None] = []

    def answer(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        cursors.append(cursor)
        start = int(cursor) if cursor else 0
        end = start + int(request.url.params["limit"])
        return httpx.Response(
            200,
            json={
                "data": messages[start:end],
                "metadata": {
                    "has_more": end < len(messages),
                    "next_cursor": str(end),
                    "limit": end - start,
                },
            },
        )

    async with rest_client_over(answer) as rest:
        capture = ReplyCapture("room-1", user_ops=UserOps(rest))
        capture._on_message_updated(_delivery_update("processing"))
        with pytest.raises(TimeoutError, match="last delivery status: processing"):
            await capture.wait_for_processed(MSG, AGENT, deadline_s=0.01)

    assert "rest=failed rest_error=None" in caplog.text
    assert "private note" not in caplog.text
    assert cursors == [None, "100"]


@pytest.mark.parametrize("status,error", [(400, "ApiError"), (200, "ParsingError")])
async def test_snapshot_http_failure_preserves_delivery_timeout_and_omits_body(
    status: int,
    error: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = "private provider exception body"
    async with rest_client_over(
        lambda _: httpx.Response(status, json={"message": body})
    ) as rest:
        capture = ReplyCapture("room-1", user_ops=UserOps(rest))
        capture._on_message_updated(_delivery_update("processing"))

        with pytest.raises(TimeoutError, match="last delivery status: processing"):
            await capture.wait_for_processed(MSG, AGENT, deadline_s=0.01)

    assert f"rest=None rest_error={error}" in caplog.text
    assert body not in caplog.text


async def test_snapshot_deadline_preserves_delivery_timeout(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(capture_module, "DELIVERY_SNAPSHOT_TIMEOUT_S", 0.02)
    cancelled = asyncio.Event()

    async def stalled_response(_request: httpx.Request) -> httpx.Response:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        raise AssertionError("unreachable")

    async with rest_client_over(stalled_response) as rest:
        capture = ReplyCapture("room-1", user_ops=UserOps(rest))
        with pytest.raises(TimeoutError, match="last delivery status: none"):
            await asyncio.wait_for(
                capture.wait_for_processed(MSG, AGENT, deadline_s=0.01), timeout=1
            )

    assert cancelled.is_set()
    assert "rest=None rest_error=TimeoutError" in caplog.text


async def test_cancellation_during_snapshot_propagates() -> None:
    requested = asyncio.Event()

    async def stalled_response(_request: httpx.Request) -> httpx.Response:
        requested.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async with rest_client_over(stalled_response) as rest:
        capture = ReplyCapture("room-1", user_ops=UserOps(rest))
        waiter = asyncio.create_task(
            capture.wait_for_processed(MSG, AGENT, deadline_s=0.01)
        )
        try:
            await asyncio.wait_for(requested.wait(), timeout=1)
        finally:
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
