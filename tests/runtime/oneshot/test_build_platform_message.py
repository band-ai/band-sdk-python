"""OneShotInvoker: building a PlatformMessage from a forwarded payload."""

from __future__ import annotations

from band.runtime.oneshot import (
    _build_platform_message,
)


class TestBuildPlatformMessage:
    def test_basic(self) -> None:
        payload = {
            "id": "m1",
            "content": "hello",
            "sender_id": "u1",
            "sender_type": "User",
            "message_type": "user",
            "inserted_at": "2026-05-21T10:00:00Z",
        }
        msg = _build_platform_message(payload, "r1", "Alice", [])
        assert msg.id == "m1"
        assert msg.room_id == "r1"
        assert msg.content == "hello"
        assert msg.sender_id == "u1"
        assert msg.sender_type == "User"
        assert msg.sender_name == "Alice"
        assert msg.message_type == "user"
        assert msg.created_at.year == 2026

    def test_defaults_for_missing_fields(self) -> None:
        msg = _build_platform_message({"id": "m1"}, "r1", None, [])
        assert msg.content == ""
        assert msg.sender_id == ""
        assert msg.sender_type == "User"
        assert msg.message_type == "user"

    def test_translates_uuid_mentions_via_participants(self) -> None:
        # Mirrors DefaultPreprocessor: the platform normalizes mentions to
        # @[[uuid]]; the turn the LLM sees must carry @handle instead.
        payload = {"id": "m1", "content": "@[[agent-9]] remember this about me"}
        participants = [{"id": "agent-9", "name": "Bot", "handle": "org/bot"}]
        msg = _build_platform_message(payload, "r1", "Alice", participants)
        assert msg.content == "@org/bot remember this about me"
