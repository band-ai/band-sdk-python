"""Live reply capture reads durable delivery state over REST."""

from __future__ import annotations

import httpx
import pytest

from band.client.streaming import DeliveryStatus
from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    LifecyclePlatform,
    rest_client_over,
)


class TestLiveCaptureDelivery:
    """Durable delivery status is read without observer frames."""

    async def test_live_capture_reads_durable_delivery_without_observer_frames(
        self,
    ) -> None:
        peer = LifecyclePlatform()
        peer.add_message("trigger")
        peer.messages[0]["metadata"] = {
            "delivery_status": {AGENT_ID: {"status": DeliveryStatus.PROCESSING}}
        }
        peer.add_message("unclaimed")

        def answer(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": peer.messages,
                    "metadata": {"has_more": False, "limit": 50},
                },
            )

        async with rest_client_over(answer) as rest:
            capture = ReplyCapture(ROOM_ID, user_ops=UserOps(rest))
            assert await capture.durable_delivery_statuses(
                ("trigger", "unclaimed"), AGENT_ID
            ) == {"trigger": DeliveryStatus.PROCESSING, "unclaimed": None}
            with pytest.raises(AssertionError, match="missing"):
                await capture.durable_delivery_statuses(("missing",), AGENT_ID)
