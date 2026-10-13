"""OneShotInvoker: routing a forwarded event by type."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.runtime.oneshot import (
    OneShotEnvelopeError,
)
from tests.runtime.conftest import make_link_mock, platform_msg
from tests.runtime.oneshot.kit import make_adapter_mock, make_invoker, msg_body


class TestHandleEventRouting:
    async def test_non_message_event_returns_ignored(self) -> None:
        link = make_link_mock()
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)

        result = await invoker.handle_event(
            {"event_type": "room_added", "room_id": "r1", "payload": {}}
        )
        assert result["status"] == "ignored"
        assert result["event_type"] == "room_added"
        adapter.on_event.assert_not_awaited()
        link.get_next_message.assert_not_awaited()

    async def test_room_removed_triggers_adapter_cleanup(self) -> None:
        """Regression: long-running containers keep one adapter alive across
        many rooms. Without on_cleanup on room teardown, per-room caches
        (Anthropic history, Claude SDK sessions, etc.) leak.
        """
        link = make_link_mock()
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)

        result = await invoker.handle_event(
            {"event_type": "room_removed", "room_id": "r1", "payload": {}}
        )

        adapter.on_cleanup.assert_awaited_once_with("r1")
        assert result == {
            "status": "cleaned_up",
            "event_type": "room_removed",
            "room_id": "r1",
        }
        link.get_next_message.assert_not_awaited()

    async def test_room_deleted_triggers_adapter_cleanup(self) -> None:
        link = make_link_mock()
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)

        await invoker.handle_event(
            {"event_type": "room_deleted", "room_id": "r1", "payload": {}}
        )

        adapter.on_cleanup.assert_awaited_once_with("r1")

    async def test_room_removed_falls_back_to_payload_id(self) -> None:
        link = make_link_mock()
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)

        await invoker.handle_event(
            {"event_type": "room_removed", "payload": {"id": "r-payload"}}
        )

        adapter.on_cleanup.assert_awaited_once_with("r-payload")

    async def test_room_removed_reports_failed_cleanup(self) -> None:
        link = make_link_mock()
        adapter = make_adapter_mock()
        adapter.on_cleanup = AsyncMock(side_effect=RuntimeError("adapter blew up"))
        invoker = await make_invoker(link, adapter)

        with pytest.raises(RuntimeError, match="adapter blew up"):
            await invoker.handle_event(
                {"event_type": "room_removed", "room_id": "r1", "payload": {}}
            )

    async def test_room_removed_with_no_resolvable_room_id_raises_envelope_error(
        self,
    ) -> None:
        """A room-cleanup event with no room identity anywhere (envelope nor
        payload) is a malformed envelope, not a silent no-op — unlike the
        pre-band_sdk_core behavior, which returned ``{"status": "cleaned_up",
        "room_id": None}`` without calling ``on_cleanup``.
        """
        link = make_link_mock()
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)

        with pytest.raises(OneShotEnvelopeError, match="room_id"):
            await invoker.handle_event({"event_type": "room_removed", "payload": {}})
        adapter.on_cleanup.assert_not_awaited()

    async def test_missing_room_id_raises_envelope_error(self) -> None:
        link = make_link_mock()
        invoker = await make_invoker(link)
        body = msg_body()
        del body["room_id"]
        with pytest.raises(OneShotEnvelopeError, match="room_id"):
            await invoker.handle_event(body)

    async def test_missing_message_id_raises_envelope_error(self) -> None:
        link = make_link_mock()
        invoker = await make_invoker(link)
        body = {
            "event_type": "message_created",
            "agent_id": "agent-1",
            "room_id": "room-1",
            "payload": {"sender_id": "u", "content": "x"},
        }
        with pytest.raises(OneShotEnvelopeError):
            await invoker.handle_event(body)

    async def test_falls_back_to_payload_chat_room_id(self) -> None:
        link = make_link_mock(next_messages=[platform_msg("msg-1"), None])
        invoker = await make_invoker(link)
        body = msg_body()
        del body["room_id"]
        body["payload"]["chat_room_id"] = "fallback-room"

        result = await invoker.handle_event(body)
        assert result["room_id"] == "fallback-room"
