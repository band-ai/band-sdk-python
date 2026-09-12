"""Tests for the delivery-vs-provider-failure misclassification guard."""

from __future__ import annotations

import pytest

from band.core.delivery import DeliveryFailedError, deliver_reply
from band.testing.fake_tools import FakeAgentTools


class TestDeliverReply:
    async def test_forwards_a_successful_send(self) -> None:
        tools = FakeAgentTools()

        result = await deliver_reply(tools, "hello", mentions=["@alice"])

        assert tools.messages_sent[0]["content"] == "hello"
        assert result["content"] == "hello"

    async def test_wraps_a_send_message_failure(self) -> None:
        """A raised send_message must become DeliveryFailedError, not
        propagate as-is -- so a shared except block reading for a provider
        failure can tell delivery and provider failures apart."""
        tools = FakeAgentTools()

        with pytest.raises(DeliveryFailedError) as exc_info:
            # FakeAgentTools.send_message raises BandToolError for a
            # mention-less send, mirroring the real platform requirement.
            await deliver_reply(tools, "hello", mentions=None)

        assert exc_info.value.__cause__ is exc_info.value.cause
        assert "mention" in str(exc_info.value.cause).lower()
