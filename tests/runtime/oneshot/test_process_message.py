"""OneShotInvoker: processing one forwarded message."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests.runtime.conftest import make_link_mock, platform_msg
from tests.runtime.oneshot.kit import make_adapter_mock, make_invoker, msg_body


class TestProcessMessage:
    async def test_processes_message(self) -> None:
        link = make_link_mock(
            participants=[
                {"id": "user-1", "name": "Alice", "type": "User", "handle": "alice"},
            ],
            next_messages=[platform_msg("msg-1"), None],
        )
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)

        result = await invoker.handle_event(msg_body())

        assert result["status"] == "done"
        assert result["room_id"] == "room-1"
        assert result["message_id"] == "msg-1"

        adapter.on_event.assert_awaited_once()
        inp = adapter.received_input
        assert inp.msg.id == "msg-1"
        assert inp.msg.sender_name == "Alice"

        link.mark_processing.assert_awaited_once_with("room-1", "msg-1")
        link.mark_processed.assert_awaited_once_with("room-1", "msg-1")
        link.mark_failed.assert_not_awaited()

    async def test_skips_self_message(self) -> None:
        link = make_link_mock()
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)
        body = msg_body(
            msg_id="msg-self",
            sender_id="agent-1",
            sender_type="Agent",
            content="echo",
        )

        result = await invoker.handle_event(body)
        assert result["status"] == "skipped_self"
        adapter.on_event.assert_not_awaited()
        link.get_next_message.assert_not_awaited()
        link.mark_processing.assert_not_awaited()

    async def test_skips_when_no_pending(self) -> None:
        """get_next_message returns None — already processed by a sibling."""
        link = make_link_mock(next_messages=[None])
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)

        result = await invoker.handle_event(msg_body())

        assert result["status"] == "no_pending"
        assert result["message_id"] == "msg-1"
        adapter.on_event.assert_not_awaited()
        link.mark_processing.assert_not_awaited()

    async def test_skips_when_different_message_is_next(self) -> None:
        link = make_link_mock(next_messages=[platform_msg("msg-other")])
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)

        result = await invoker.handle_event(msg_body(msg_id="msg-1"))

        assert result["status"] == "already_processed"
        assert result["message_id"] == "msg-1"
        assert result["next_open"] == "msg-other"
        adapter.on_event.assert_not_awaited()
        link.mark_processing.assert_not_awaited()

    async def test_claim_propagates_get_next_message_failure(self) -> None:
        """Regression: a transient ``/next`` failure at the claim step must
        not be silently treated as ``no_pending`` — that would leave the
        message open on the platform and tell the bridge it was handled.
        """
        link = make_link_mock()
        link.get_next_message = AsyncMock(side_effect=RuntimeError("network down"))
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)

        with pytest.raises(RuntimeError, match="network down"):
            await invoker.handle_event(msg_body(msg_id="msg-1"))

        # No claim attempted, no adapter run — caller can retry.
        link.mark_processing.assert_not_awaited()
        adapter.on_event.assert_not_awaited()

    async def test_marks_failed_on_adapter_error(self) -> None:
        link = make_link_mock(next_messages=[platform_msg("msg-1")])
        adapter = make_adapter_mock()
        adapter.on_event = AsyncMock(side_effect=RuntimeError("LLM crashed"))
        invoker = await make_invoker(link, adapter)

        with pytest.raises(RuntimeError, match="LLM crashed"):
            await invoker.handle_event(msg_body())

        link.mark_processing.assert_awaited_once_with("room-1", "msg-1")
        link.mark_failed.assert_awaited_once()
        link.mark_processed.assert_not_awaited()
