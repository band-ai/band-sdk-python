"""Tests for the typed OpenCode SSE event layer (`parse_opencode_event`)."""

from __future__ import annotations

from band.core.types import TurnUsage
from band.integrations.opencode import (
    MessagePartDeltaEvent,
    MessagePartUpdatedEvent,
    MessageUpdatedEvent,
    OpencodeErrorInfo,
    OpencodeToolState,
    PermissionAskedEvent,
    QuestionAskedEvent,
    SessionErrorEvent,
    SessionIdleEvent,
    UnknownOpencodeEvent,
    describe_error,
    parse_opencode_event,
)


class TestKnownEventParsing:
    def test_message_updated_with_wire_aliases(self) -> None:
        event = parse_opencode_event(
            {
                "type": "message.updated",
                "properties": {
                    "info": {
                        "id": "msg-1",
                        "sessionID": "sess-1",
                        "role": "assistant",
                        "tokens": {
                            "input": 10,
                            "output": 5,
                            "reasoning": 3,
                            "cache": {"read": 1, "write": 2},
                        },
                    }
                },
            }
        )

        assert isinstance(event, MessageUpdatedEvent)
        assert event.session_id == "sess-1"
        info = event.properties.info
        assert info is not None
        assert info.id == "msg-1"
        assert info.role == "assistant"
        assert info.tokens is not None
        assert info.tokens.to_turn_usage() == TurnUsage(
            input_tokens=10,
            output_tokens=8,  # reasoning folds into output
            cache_read_tokens=1,
            cache_write_tokens=2,
        )

    def test_message_part_updated_reads_part_session(self) -> None:
        event = parse_opencode_event(
            {
                "type": "message.part.updated",
                "properties": {
                    "part": {
                        "id": "part-1",
                        "sessionID": "sess-2",
                        "messageID": "msg-2",
                        "type": "tool",
                        "tool": "band_send_message",
                        "callID": "call-1",
                        "state": {"status": "completed", "output": "sent"},
                    }
                },
            }
        )

        assert isinstance(event, MessagePartUpdatedEvent)
        assert event.session_id == "sess-2"
        part = event.properties.part
        assert part is not None
        assert part.call_id == "call-1"
        assert part.state is not None
        assert part.state.reported_output == "sent"

    def test_message_part_delta_reads_flat_properties(self) -> None:
        event = parse_opencode_event(
            {
                "type": "message.part.delta",
                "properties": {
                    "sessionID": "sess-3",
                    "messageID": "msg-3",
                    "partID": "part-3",
                    "field": "text",
                    "delta": "hel",
                },
            }
        )

        assert isinstance(event, MessagePartDeltaEvent)
        assert event.session_id == "sess-3"
        assert event.properties.part_id == "part-3"
        assert event.properties.delta == "hel"

    def test_permission_asked_carries_verified_shape(self) -> None:
        event = parse_opencode_event(
            {
                "type": "permission.asked",
                "properties": {
                    "id": "perm-1",
                    "sessionID": "sess-4",
                    "permission": "band_store_memory",
                    "patterns": ["*"],
                    "metadata": {},
                    "always": ["*"],
                    "tool": {"messageID": "msg-4", "callID": "call-4"},
                },
            }
        )

        assert isinstance(event, PermissionAskedEvent)
        assert event.session_id == "sess-4"
        request = event.properties
        assert request.id == "perm-1"
        assert request.permission == "band_store_memory"
        assert request.patterns == ["*"]
        assert request.tool is not None
        assert request.tool.call_id == "call-4"

    def test_question_asked_parses_question_list(self) -> None:
        event = parse_opencode_event(
            {
                "type": "question.asked",
                "properties": {
                    "id": "q-1",
                    "sessionID": "sess-5",
                    "questions": [{"question": "Which color?"}, {}],
                },
            }
        )

        assert isinstance(event, QuestionAskedEvent)
        assert event.session_id == "sess-5"
        assert [q.question for q in event.properties.questions] == [
            "Which color?",
            "Question",
        ]

    def test_session_error_and_idle(self) -> None:
        error_event = parse_opencode_event(
            {
                "type": "session.error",
                "properties": {
                    "sessionID": "sess-6",
                    "error": {"name": "APIError", "data": {"message": "boom"}},
                },
            }
        )
        idle_event = parse_opencode_event(
            {"type": "session.idle", "properties": {"sessionID": "sess-6"}}
        )

        assert isinstance(error_event, SessionErrorEvent)
        assert error_event.session_id == "sess-6"
        assert describe_error(error_event.properties.error) == "APIError: boom"
        assert isinstance(idle_event, SessionIdleEvent)
        assert idle_event.session_id == "sess-6"


class TestDegradation:
    def test_unknown_event_type_becomes_unknown(self) -> None:
        event = parse_opencode_event({"type": "server.connected", "properties": {}})
        assert isinstance(event, UnknownOpencodeEvent)
        assert event.type == "server.connected"
        assert event.session_id is None

    def test_missing_type_becomes_unknown(self) -> None:
        assert isinstance(parse_opencode_event({}), UnknownOpencodeEvent)

    def test_malformed_known_type_becomes_unknown(self) -> None:
        # properties as a string cannot validate any known payload
        event = parse_opencode_event(
            {"type": "permission.asked", "properties": "garbage"}
        )
        assert isinstance(event, UnknownOpencodeEvent)
        assert event.raw == {"type": "permission.asked", "properties": "garbage"}

    def test_garbage_in_one_corner_degrades_only_that_corner(self) -> None:
        """A malformed nested payload (tokens) must not fail the whole event —
        the assistant id must still register so text parts keep flowing."""
        event = parse_opencode_event(
            {
                "type": "message.updated",
                "properties": {
                    "info": {
                        "id": "msg-7",
                        "sessionID": "sess-7",
                        "role": "assistant",
                        "tokens": {"input": "not-a-number"},
                    }
                },
            }
        )

        assert isinstance(event, MessageUpdatedEvent)
        info = event.properties.info
        assert info is not None
        assert info.id == "msg-7"
        assert info.tokens is None

    def test_junk_permission_patterns_degrade_to_default(self) -> None:
        event = parse_opencode_event(
            {
                "type": "permission.asked",
                "properties": {
                    "id": "perm-2",
                    "sessionID": "sess-8",
                    "permission": "doom_loop",
                    "patterns": "not-a-list",
                },
            }
        )

        assert isinstance(event, PermissionAskedEvent)
        assert event.properties.id == "perm-2"
        assert event.properties.permission == "doom_loop"
        assert event.properties.patterns == []

    def test_mixed_permission_patterns_stringify_and_drop_nones(self) -> None:
        event = parse_opencode_event(
            {
                "type": "permission.asked",
                "properties": {
                    "id": "perm-3",
                    "sessionID": "sess-8",
                    "permission": "bash",
                    "patterns": ["rm -rf tmp", None, 42],
                },
            }
        )

        assert isinstance(event, PermissionAskedEvent)
        assert event.properties.patterns == ["rm -rf tmp", "42"]

    def test_junk_question_list_degrades_to_empty(self) -> None:
        event = parse_opencode_event(
            {
                "type": "question.asked",
                "properties": {
                    "id": "q-2",
                    "sessionID": "sess-8",
                    "questions": "what?",
                },
            }
        )

        assert isinstance(event, QuestionAskedEvent)
        assert event.properties.questions == []


class TestToolStateOutputPresence:
    def test_present_falsy_output_is_preserved(self) -> None:
        state = OpencodeToolState.model_validate({"status": "completed", "output": 0})
        assert state.has_output
        assert state.reported_output == 0

    def test_absent_output_reports_empty_string(self) -> None:
        state = OpencodeToolState.model_validate({"status": "completed"})
        assert not state.has_output
        assert state.reported_output == ""


class TestErrorDescriptions:
    def test_none_error_is_unknown(self) -> None:
        assert describe_error(None) == "OpenCode reported an unknown error."

    def test_error_without_message_uses_name(self) -> None:
        error = OpencodeErrorInfo.model_validate({"name": "ProviderAuthError"})
        assert describe_error(error) == (
            "ProviderAuthError: OpenCode reported an error."
        )

    def test_error_without_name_uses_default(self) -> None:
        error = OpencodeErrorInfo.model_validate({"data": {"message": "nope"}})
        assert describe_error(error) == "OpenCodeError: nope"
