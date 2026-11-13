"""A delivery barrier stops at a failure the running agent will not retry."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus, MessageCreatedPayload
from tests.e2e.baseline.toolkit.capture import DeliveryFailedError, ReplyCapture

MSG = "msg-1"
AGENT = "agent-1"
ERROR = "Internal error while processing message"
# Far beyond the virtual clock's reach before the barrier must decide.
DEADLINE_S = 300.0

FAILED_ONCE = (DeliveryStatus.PROCESSING, DeliveryStatus.FAILED)
FAILED_TWICE = (*FAILED_ONCE, DeliveryStatus.PROCESSING, DeliveryStatus.FAILED)
RETRIED = (*FAILED_ONCE, DeliveryStatus.PROCESSING, DeliveryStatus.PROCESSED)


def delivery_update(
    status: DeliveryStatus, *attempts: DeliveryStatus
) -> MessageCreatedPayload:
    """Build the cumulative attempt snapshot the platform broadcasts."""
    return MessageCreatedPayload(
        id=MSG,
        content="(barrier message)",
        message_type="text",
        metadata={
            "delivery_status": {
                AGENT: {
                    "status": status.value,
                    "current_attempt": len(attempts),
                    "attempts": [
                        {
                            "attempt_number": number,
                            "status": attempt.value,
                            "error": ERROR
                            if attempt is DeliveryStatus.FAILED
                            else None,
                        }
                        for number, attempt in enumerate(attempts, start=1)
                    ],
                }
            }
        },
        sender_id="user-1",
        sender_type="User",
        inserted_at="t",
        updated_at="t",
    )


def capture_after(*statuses: DeliveryStatus) -> ReplyCapture:
    """Observe cumulative snapshots for each delivery transition."""
    capture = ReplyCapture("room-1", deadline_s=DEADLINE_S)
    attempts: list[DeliveryStatus] = []
    for status in statuses:
        if status is DeliveryStatus.PROCESSING:
            attempts.append(status)
        else:
            attempts[-1] = status
        capture._on_message_updated(delivery_update(status, *attempts))
    return capture


class TestFailedDeliveryBarrier:
    @pytest.mark.looptime
    @pytest.mark.parametrize("waiter", ["wait_for_processed", "wait_for_reply"])
    async def test_a_failure_without_an_expected_retry_raises_at_once(
        self, waiter: str
    ) -> None:
        capture = capture_after(*FAILED_ONCE)

        with pytest.raises(DeliveryFailedError, match=ERROR):
            await getattr(capture, waiter)(MSG, AGENT)

    @pytest.mark.looptime
    async def test_a_failure_within_the_expected_retries_is_waited_through(
        self,
    ) -> None:
        capture = capture_after(*FAILED_ONCE)

        with pytest.raises(TimeoutError, match="last delivery status: failed"):
            await capture.wait_for_processed(MSG, AGENT, retries=1)

    @pytest.mark.looptime
    async def test_a_failure_beyond_the_expected_retries_raises(self) -> None:
        capture = capture_after(*FAILED_TWICE)

        with pytest.raises(DeliveryFailedError, match="expected retries: 1"):
            await capture.wait_for_processed(MSG, AGENT, retries=1)

    async def test_a_retried_message_that_is_processed_passes(self) -> None:
        capture = capture_after(*RETRIED)

        await capture.wait_for_processed(MSG, AGENT, retries=1)

        assert capture.delivery_status(MSG, AGENT) is DeliveryStatus.PROCESSED

    @pytest.mark.looptime
    @pytest.mark.parametrize("waiter", ["wait_for_processed", "wait_for_reply"])
    async def test_cumulative_failures_exhaust_retries_without_intermediate_frames(
        self, waiter: str
    ) -> None:
        capture = ReplyCapture("room-1", deadline_s=DEADLINE_S)
        capture._on_message_updated(
            delivery_update(
                DeliveryStatus.FAILED, DeliveryStatus.FAILED, DeliveryStatus.FAILED
            )
        )

        with pytest.raises(DeliveryFailedError, match="expected retries: 1"):
            await getattr(capture, waiter)(MSG, AGENT, retries=1)

    @pytest.mark.looptime
    @pytest.mark.parametrize("waiter", ["wait_for_processed", "wait_for_reply"])
    async def test_duplicate_snapshots_do_not_spend_another_retry(
        self, waiter: str
    ) -> None:
        capture = capture_after(*FAILED_ONCE)
        capture._on_message_updated(
            delivery_update(DeliveryStatus.FAILED, DeliveryStatus.FAILED)
        )

        with pytest.raises(TimeoutError):
            await getattr(capture, waiter)(MSG, AGENT, retries=1)
