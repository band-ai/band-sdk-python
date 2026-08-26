"""Proves `WirePayload.from_wire` against the real, installed band_sdk_core --
not a vendored corpus snapshot. band_sdk_core is a real runtime dependency of
this SDK (not test-only), so there is nothing to fake here: every case below
builds a payload, calls the actual Rust-backed validator, and asserts on
what it actually did. No generated data file, no JSON -- every case is a
plain Python function a reviewer can read top to bottom.

Each `make_*` factory returns a valid baseline payload for one event type;
tests mutate or delete individual keys to exercise one rule at a time.
"""

from __future__ import annotations

from typing import Any

import pytest

from band.client.streaming.client import (
    AgentControlPayload,
    ContactAddedPayload,
    ContactRequestReceivedPayload,
    MessageCreatedPayload,
    ParticipantRemovedPayload,
    RoomRemovedPayload,
    SupersedePayload,
    WireEvent,
    _PAYLOAD_MODELS,
)
from band_sdk_core import EventType


def make_message_created(**overrides: Any) -> dict[str, Any]:
    payload = {
        "id": "msg-1",
        "content": "hello @sage",
        "message_type": "text",
        "sender_id": "user-1",
        "sender_type": "User",
        "inserted_at": "2026-08-01T10:00:00Z",
        "updated_at": "2026-08-01T10:00:00Z",
        "metadata": {"mentions": [{"id": "agent-1", "handle": "sage"}]},
    }
    payload.update(overrides)
    return payload


def make_room_removed(**overrides: Any) -> dict[str, Any]:
    payload = {
        "id": "room-1",
        "inserted_at": "2026-08-01T09:00:00Z",
        "updated_at": "2026-08-01T09:05:00Z",
    }
    payload.update(overrides)
    return payload


def make_participant_removed(**overrides: Any) -> dict[str, Any]:
    payload = {"id": "user-1", "name": "Test User", "type": "User"}
    payload.update(overrides)
    return payload


def make_contact_request_received(**overrides: Any) -> dict[str, Any]:
    payload = {
        "id": "req-1",
        "status": "pending",
        "inserted_at": "2026-08-01T10:00:00Z",
        "from_handle": "john_doe",
        "from_name": "John Doe",
    }
    payload.update(overrides)
    return payload


def make_contact_added(**overrides: Any) -> dict[str, Any]:
    payload = {
        "id": "contact-1",
        "type": "User",
        "inserted_at": "2026-08-01T10:00:00Z",
        "handle": "jane_smith",
        "name": "Jane Smith",
    }
    payload.update(overrides)
    return payload


def make_supersede(**overrides: Any) -> dict[str, Any]:
    payload = {
        "reason": "session.already_connected",
        "message": "superseded by a newer session",
        "correlation_id": "evict-1",
    }
    payload.update(overrides)
    return payload


def make_agent_control(**overrides: Any) -> dict[str, Any]:
    payload = {
        "mode": "interrupt",
        "scope": "agent",
        "agent_id": "agent-1",
        "correlation_id": "ctl-1",
        "execution_id": None,
        "room_id": None,
    }
    payload.update(overrides)
    return payload


# --- message_created: nested metadata.mentions hydration ---


def test_message_created_hydrates_nested_mention_as_a_real_model() -> None:
    payload = MessageCreatedPayload.from_wire(
        WireEvent.MESSAGE_CREATED, make_message_created()
    )

    mention = payload.metadata.mentions[0]
    assert mention.__class__.__name__ == "Mention"
    assert mention.id == "agent-1"
    assert mention.handle == "sage"


def test_message_created_explicit_null_mentions_normalizes_to_empty_list() -> None:
    payload = MessageCreatedPayload.from_wire(
        WireEvent.MESSAGE_CREATED, make_message_created(metadata={"mentions": None})
    )

    assert payload.metadata.mentions == []


def test_message_created_rejects_a_non_object_mention_item() -> None:
    with pytest.raises(ValueError) as exc_info:
        MessageCreatedPayload.from_wire(
            WireEvent.MESSAGE_CREATED,
            make_message_created(metadata={"mentions": [1]}),
        )

    assert exc_info.value.issues == (
        (
            "metadata.mentions[0]",
            "wrong_type",
            "`metadata.mentions[0]` must be an object",
        ),
    )


def test_message_created_rejects_a_mention_missing_id() -> None:
    with pytest.raises(ValueError) as exc_info:
        MessageCreatedPayload.from_wire(
            WireEvent.MESSAGE_CREATED,
            make_message_created(metadata={"mentions": [{"handle": "sage"}]}),
        )

    assert exc_info.value.issues == (
        (
            "metadata.mentions[0].id",
            "missing",
            "required field `metadata.mentions[0].id` is missing",
        ),
    )


# --- room_removed: shares room_added's canonical shape ---


def test_room_removed_accepts_the_room_added_shape() -> None:
    payload = RoomRemovedPayload.from_wire(WireEvent.ROOM_REMOVED, make_room_removed())
    assert payload.id == "room-1"


def test_room_removed_rejects_missing_timestamps() -> None:
    raw = make_room_removed()
    del raw["inserted_at"]
    del raw["updated_at"]

    with pytest.raises(ValueError) as exc_info:
        RoomRemovedPayload.from_wire(WireEvent.ROOM_REMOVED, raw)

    issue_paths = {path for path, _code, _msg in exc_info.value.issues}
    assert issue_paths == {"inserted_at", "updated_at"}


# --- participant_removed: stricter than this SDK's pre-band-sdk-core rule ---


def test_participant_removed_accepts_name_and_type() -> None:
    payload = ParticipantRemovedPayload.from_wire(
        WireEvent.PARTICIPANT_REMOVED, make_participant_removed()
    )
    assert payload.name == "Test User"
    assert payload.type == "User"


def test_participant_removed_rejects_id_only() -> None:
    with pytest.raises(ValueError) as exc_info:
        ParticipantRemovedPayload.from_wire(
            WireEvent.PARTICIPANT_REMOVED, {"id": "user-1"}
        )

    issue_paths = {path for path, _code, _msg in exc_info.value.issues}
    assert issue_paths == {"name", "type"}


# --- contact_request_received: sender fields may be genuinely absent ---


def test_contact_request_received_accepts_absent_sender() -> None:
    raw = make_contact_request_received()
    del raw["from_handle"]
    del raw["from_name"]

    payload = ContactRequestReceivedPayload.from_wire(
        WireEvent.CONTACT_REQUEST_RECEIVED, raw
    )

    assert payload.from_handle is None
    assert payload.from_name is None


# --- contact_added: handle/name may be an explicit wire null ---


def test_contact_added_accepts_explicit_null_handle_and_name() -> None:
    payload = ContactAddedPayload.from_wire(
        WireEvent.CONTACT_ADDED,
        make_contact_added(handle=None, name=None),
    )

    assert payload.handle is None
    assert payload.name is None


def test_contact_added_syncs_the_legacy_is_external_alias() -> None:
    payload = ContactAddedPayload.from_wire(
        WireEvent.CONTACT_ADDED, make_contact_added(is_external=True)
    )

    assert payload.is_remote is True
    assert payload.is_external is True


# --- supersede: retryable/retry_after are lenient, never rejected ---


def test_supersede_retryable_absent_defaults_to_false() -> None:
    payload = SupersedePayload.from_wire(WireEvent.SUPERSEDE, make_supersede())
    assert payload.retryable is False


def test_supersede_non_bool_retryable_normalizes_to_false() -> None:
    payload = SupersedePayload.from_wire(
        WireEvent.SUPERSEDE, make_supersede(retryable="true")
    )
    assert payload.retryable is False


def test_supersede_malformed_retry_after_normalizes_to_none() -> None:
    payload = SupersedePayload.from_wire(
        WireEvent.SUPERSEDE, make_supersede(retry_after="soon")
    )
    assert payload.retry_after is None


# --- agent.control: mode/scope are closed sets ---


def test_agent_control_accepts_a_known_mode() -> None:
    payload = AgentControlPayload.from_wire(
        WireEvent.AGENT_CONTROL, make_agent_control()
    )
    assert payload.mode == "interrupt"


def test_agent_control_rejects_an_unknown_mode() -> None:
    with pytest.raises(ValueError) as exc_info:
        AgentControlPayload.from_wire(
            WireEvent.AGENT_CONTROL, make_agent_control(mode="pause")
        )

    path, code, _msg = exc_info.value.issues[0]
    assert (path, code) == ("mode", "invalid_value")


# --- registry consistency ---


def test_every_registered_event_name_is_known_to_band_sdk_core() -> None:
    for event_name in _PAYLOAD_MODELS:
        assert EventType.from_wire_name(event_name) is not None, event_name
