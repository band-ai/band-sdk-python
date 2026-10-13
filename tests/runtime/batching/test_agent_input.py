"""A turn's originals keep their own identity; a singleton turn is the default."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from band.core.types import AgentInput, HistoryProvider, PlatformMessage


def message(message_id: str, sender_id: str) -> PlatformMessage:
    return PlatformMessage(
        id=message_id,
        room_id="room-1",
        content=message_id,
        sender_id=sender_id,
        sender_type="User",
        sender_name=None,
        message_type="text",
        metadata={},
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def turn_input(*messages: PlatformMessage) -> AgentInput:
    return AgentInput(
        msg=messages[0],
        messages=messages,
        tools=None,  # type: ignore[arg-type]
        history=HistoryProvider(raw=[]),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id="room-1",
    )


class TestAgentInput:
    def test_a_turn_without_explicit_originals_is_the_singleton_of_its_message(
        self,
    ) -> None:
        only = message("m1", "alice")

        assert turn_input(only).messages == (only,)
        assert replace(turn_input(only), messages=()).messages == (only,)

    def test_senders_are_distinct_in_order_of_first_appearance(self) -> None:
        originals = [
            message("m1", "alice"),
            message("m2", "bob"),
            message("m3", "alice"),
        ]

        assert turn_input(*originals).sender_ids == ("alice", "bob")

    def test_a_message_without_a_sender_adds_no_audience(self) -> None:
        originals = [message("m1", ""), message("m2", "bob")]

        assert turn_input(*originals).sender_ids == ("bob",)
