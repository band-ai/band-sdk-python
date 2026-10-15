from __future__ import annotations

import pytest
from pydantic import ValidationError

from band.runtime.tools import (
    AddParticipantInput,
    CreateChatroomInput,
    GetParticipantsInput,
    LookupPeersInput,
    RemoveParticipantInput,
    SendEventInput,
    SendMessageInput,
    SendRoomFileInput,
)


class TestToolInputModels:
    """Test Pydantic tool input models."""

    def test_send_message_input_validation(self) -> None:
        """SendMessageInput should validate fields."""
        model = SendMessageInput(content="Hello", mentions=["User"])
        assert model.content == "Hello"
        assert model.mentions == ["User"]

    def test_send_message_input_accepts_empty_mentions(self) -> None:
        """SendMessageInput allows empty mentions (runtime validates instead)."""
        model = SendMessageInput(content="Hello", mentions=[])
        assert model.mentions == []

    def test_send_room_file_input_requires_mentions(self) -> None:
        """band_send_room_file posts a message to attach the file, so its
        schema must require mentions just like SendMessageInput -- an
        optional-looking field here would let the LLM omit it, upload the
        file, and only then discover the platform's mention requirement."""
        with pytest.raises(ValidationError):
            SendRoomFileInput(content="hi", filename="f.txt")

    def test_send_room_file_input_accepts_empty_mentions(self) -> None:
        """Present but empty is still valid at the schema level (runtime
        validates the "at least one" rule instead, same as SendMessageInput)."""
        model = SendRoomFileInput(content="hi", filename="f.txt", mentions=[])
        assert model.mentions == []

    def test_send_event_input_validation(self) -> None:
        """SendEventInput should validate fields."""
        model = SendEventInput(content="Thinking", message_type="thought")
        assert model.message_type == "thought"

    def test_send_event_input_validates_type(self) -> None:
        """SendEventInput should validate message_type."""
        with pytest.raises(ValidationError, match="literal_error"):
            SendEventInput(content="Test", message_type="invalid")

    def test_add_participant_input_defaults(self) -> None:
        """AddParticipantInput should have default role."""
        model = AddParticipantInput(identifier="User")
        assert model.role == "member"

    def test_add_participant_input_accepts_legacy_name_field(self) -> None:
        """AddParticipantInput should accept 'name' as alias for backward compat."""
        model = AddParticipantInput.model_validate({"name": "Agent Two"})
        assert model.identifier == "Agent Two"

    def test_remove_participant_input_accepts_legacy_name_field(self) -> None:
        """RemoveParticipantInput should accept 'name' as alias for backward compat."""

        model = RemoveParticipantInput.model_validate({"name": "User One"})
        assert model.identifier == "User One"

    def test_lookup_peers_input_defaults(self) -> None:
        """LookupPeersInput should have defaults."""
        model = LookupPeersInput()
        assert model.page == 1
        assert model.page_size == 50

    def test_get_participants_input_no_fields(self) -> None:
        """GetParticipantsInput should have no required fields."""
        model = GetParticipantsInput()
        assert model is not None

    def test_create_chatroom_input_validation(self) -> None:
        """CreateChatroomInput should allow optional task_id."""
        model = CreateChatroomInput(task_id="task-123")
        assert model.task_id == "task-123"

    def test_create_chatroom_input_no_task_id(self) -> None:
        """CreateChatroomInput should work without task_id."""
        model = CreateChatroomInput()
        assert model.task_id is None
