"""Wire validation and lossless structured content."""

from __future__ import annotations

import pytest
from bandpap.wire import (
    Context,
    DirectConversation,
    ErrorBody,
    Event,
    EventPage,
    Message,
    MessageEvent,
    Operation,
    ReadQuery,
    SendMessage,
)
from pydantic import ValidationError

from tests.pap.kit import event, operation, page


class TestWire:
    @pytest.mark.parametrize(
        "identifier", ["", "a" * 257, "has space", "a/b", "a=", "a\n", "é"]
    )
    def test_invalid_message_id_is_rejected(self, identifier: str) -> None:
        with pytest.raises(ValidationError):
            Message(id=identifier, sender="agent", text="hello")

    def test_role_is_not_accepted_as_provenance(self) -> None:
        request = SendMessage.model_validate(
            {
                "message": {
                    "id": "msg_one",
                    "sender": "agent",
                    "text": "hello",
                    "role": "company",
                }
            }
        )
        assert "role" not in request.wire()["message"]

    def test_data_and_context_extensions_survive_wire_roundtrip(self) -> None:
        value = {
            "message": {
                "id": "msg_one",
                "sender": "human",
                "data": {"rows": [[1, False, None, {"sku": "abc"}]]},
                "context": {"user_available": False, "units": {"temperature": "C"}},
            }
        }
        assert SendMessage.model_validate(value).wire() == value

    def test_context_only_and_empty_object_messages_are_valid(self) -> None:
        assert Message(
            id="msg_one", sender="agent", context={"user_available": False}
        ).wire()["context"] == {"user_available": False}
        Message(id="msg_two", sender="agent", data={})
        with pytest.raises(ValidationError):
            Message(id="msg_three", sender="agent")

    def test_direct_conversation_has_a_first_message(self) -> None:
        value = {
            "parent_conversation_id": "cnv_parent",
            "message": {"id": "msg_one", "sender": "human", "text": "hello"},
        }
        assert DirectConversation.model_validate(value).wire() == value

    def test_known_events_are_typed_and_unknown_events_are_skippable(self) -> None:
        result = page(
            event(
                type="message",
                message={
                    "id": "msg_reply",
                    "role": "company",
                    "sender": "human",
                    "data": {"rows": [1, 2]},
                },
            ),
            event("evt_a", type="future", private_new_field="ignored"),
        )
        assert isinstance(result.events[0], MessageEvent)
        assert type(result.events[1]) is Event
        assert result.wire()["events"][0]["message"]["data"] == {"rows": [1, 2]}
        assert "private_new_field" not in result.wire()["events"][1]
        assert (
            EventPage.model_validate_json(result.model_dump_json(serialize_as_any=True))
            == result
        )

    @pytest.mark.parametrize(
        "changes",
        [
            {"created_at": 123},
            {"created_at": "2026-01-01T00:00:00"},
            {"type": "message"},
            {"type": "authorization", "error": "insufficient_scope"},
        ],
    )
    def test_malformed_known_events_do_not_get_skipped(self, changes: dict) -> None:
        with pytest.raises(ValidationError):
            page(event(**changes))

    @pytest.mark.parametrize("wait", [True, -1, 1.5, "1"])
    def test_wait_is_a_nonnegative_integer(self, wait: object) -> None:
        with pytest.raises(ValidationError):
            ReadQuery(wait=wait)

    def test_sse_cursor_wins_and_cursors_are_not_sorted(self) -> None:
        assert ReadQuery(cursor="evt_z").resume_cursor("evt_a") == "evt_a"
        assert page(event("evt_z"), event("evt_a")).cursor == "evt_a"

    def test_operations_preserve_terms_and_validate_confirmation(self) -> None:
        value = operation(
            state="in_progress",
            confirmation={
                "revision": 1,
                "approved_by": "standing_permission",
                "confirmed_at": "2026-10-16T20:00:00Z",
            },
        )
        parsed = Operation.model_validate(value)
        assert parsed.terms == value["terms"]
        assert (
            ErrorBody.model_validate(
                {"error": "terms_changed", "operation": value}
            ).operation
            == parsed
        )
        with pytest.raises(ValidationError):
            Operation.model_validate(operation(state="in_progress"))
        with pytest.raises(ValidationError):
            Operation.model_validate(operation(state="succeeded"))

    @pytest.mark.parametrize(
        "context",
        [
            {"locale": "not a language tag!"},
            {"locale": "en_US"},
            {"time_zone": "Definitely/Not_A_Zone"},
            {"user_available": None},
            {"user_available": "true"},
            {"locale": None},
        ],
    )
    def test_invalid_known_context_does_not_cross_wire(self, context: dict) -> None:
        with pytest.raises(ValidationError):
            Context.model_validate(context)

    @pytest.mark.parametrize(
        "locale", ["en-US", "zh-Hant-TW", "x-private", "i-klingon"]
    )
    def test_valid_context_retains_original_values(self, locale: str) -> None:
        value = {
            "locale": locale,
            "time_zone": "America/Los_Angeles",
            "user_available": False,
        }
        assert Context.model_validate(value).wire() == value

    @pytest.mark.parametrize("state", ["succeeded", "failed"])
    def test_executed_outcome_requires_confirmation(self, state: str) -> None:
        with pytest.raises(ValidationError, match="confirmation"):
            Operation.model_validate(operation(state=state, result={"summary": "done"}))

    def test_expired_proposal_cannot_have_confirmation(self) -> None:
        with pytest.raises(ValidationError, match="Expired"):
            Operation.model_validate(
                operation(
                    state="expired",
                    confirmation={
                        "revision": 1,
                        "approved_by": "user",
                        "confirmed_at": "2026-10-16T20:00:00Z",
                    },
                    result={"summary": "expired"},
                )
            )
