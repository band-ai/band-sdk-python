"""Tests for the delivery-vs-provider-failure misclassification guard."""

from __future__ import annotations

import pytest

from band.core.delivery import (
    DeliveryFailedError,
    deliver_notice,
    deliver_reply,
    relay_reply,
)
from band.testing.fake_tools import FakeAgentTools


class TestDeliverReply:
    async def test_forwards_a_successful_send(self) -> None:
        tools = FakeAgentTools()

        result = await deliver_reply(tools, "hello", mentions=["@alice"])

        assert tools.messages_sent[0]["content"] == "hello"
        assert result.success is True

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


class TestDeliverNotice:
    async def test_posts_without_counting_as_the_reply(self) -> None:
        tools = FakeAgentTools()

        await deliver_notice(tools, "No pending approvals.", mentions=["@alice"])

        assert [m["content"] for m in tools.messages_sent] == ["No pending approvals."]
        assert not tools.turn.complete

    async def test_wraps_a_send_notice_failure(self) -> None:
        tools = FakeAgentTools()

        with pytest.raises(DeliveryFailedError):
            await deliver_notice(tools, "No pending approvals.", mentions=None)


class TestRelayReply:
    async def test_relays_the_final_text_as_the_turn_reply(self) -> None:
        tools = FakeAgentTools()

        assert await relay_reply(tools, "the answer", ["@alice"]) is True

        assert [m["content"] for m in tools.messages_sent] == ["the answer"]
        assert tools.turn.replied

    async def test_a_tool_reply_suppresses_the_relay(self) -> None:
        tools = FakeAgentTools()
        await tools.send_message("already answered", mentions=["@alice"])

        assert await relay_reply(tools, "closing remark", ["@alice"]) is False

        assert [m["content"] for m in tools.messages_sent] == ["already answered"]

    async def test_a_decline_suppresses_the_relay(self) -> None:
        tools = FakeAgentTools()
        await tools.no_reply("FYI only")

        assert await relay_reply(tools, "closing remark", ["@alice"]) is False

        tools.assert_no_messages_sent()

    @pytest.mark.parametrize("text", [None, "", "  \n"])
    async def test_nothing_visible_relays_nothing(self, text: str | None) -> None:
        tools = FakeAgentTools()

        assert await relay_reply(tools, text, ["@alice"]) is False

        tools.assert_no_messages_sent()
        assert not tools.turn.complete
