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


def capture_after(*statuses: DeliveryStatus) -> ReplyCapture:
    """A capture that observed ``AGENT``'s delivery of ``MSG`` move through ``statuses``."""
    capture = ReplyCapture("room-1", deadline_s=DEADLINE_S)
    for status in statuses:
        error = ERROR if status is DeliveryStatus.FAILED else None
        capture._on_message_updated(
            MessageCreatedPayload(
                id=MSG,
                content="(barrier message)",
                message_type="text",
                metadata={
                    "delivery_status": {
                        AGENT: {
                            "status": status.value,
                            "attempts": [{"status": status.value, "error": error}],
                        }
                    }
                },
                sender_id="user-1",
                sender_type="User",
                inserted_at="t",
                updated_at="t",
            )
        )
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
