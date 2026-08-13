"""Tests for the platform identity-envelope parser (INT-992).

The platform mints ``metadata["delegation"]`` server-side on cross-owner
messages. The SDK must expose it to handler/tool code as a typed, read-only
view and must never let a malformed envelope raise (a platform bug must not
take down the agent loop).
"""

from __future__ import annotations

import copy

from band.client.streaming import MessageMetadata
from band.core.delegation import (
    DelegationEnvelope,
    Originator,
    parse_delegation,
    parse_delegation_value,
)

# The platform's frozen minted shape (PLT-1075).
ORIGINATOR_UUID = "0b7a3c2e-9d1f-4e8a-b6c5-2f4a8d9e1c3b"
ENVELOPE_MESSAGE_ID = "7f3e9a1b-5c2d-4f6e-8a9b-1c3d5e7f9a2b"
ENVELOPE = {
    "version": 1,
    "originator": {
        "uuid": ORIGINATOR_UUID,
        "handle": "alice.asker",
        "display_name": "Alice Asker",
    },
    "message_id": ENVELOPE_MESSAGE_ID,
    "minted_at": "2026-08-13T09:30:00Z",
    "hop": None,
}


def make_envelope_dict() -> dict:
    """A fresh copy of the frozen platform shape (tests may mutate it)."""
    return copy.deepcopy(ENVELOPE)


class TestParseDelegationHappyPath:
    def test_parses_the_frozen_platform_shape_from_dict_metadata(self):
        metadata = {
            "mentions": [],
            "status": "sent",
            "delegation": make_envelope_dict(),
        }

        envelope = parse_delegation(metadata)

        assert isinstance(envelope, DelegationEnvelope)
        assert envelope.version == 1
        assert isinstance(envelope.originator, Originator)
        assert envelope.originator.uuid == ORIGINATOR_UUID
        assert envelope.originator.handle == "alice.asker"
        assert envelope.originator.display_name == "Alice Asker"
        assert envelope.message_id == ENVELOPE_MESSAGE_ID
        assert envelope.minted_at == "2026-08-13T09:30:00Z"
        assert envelope.hop is None

    def test_parses_from_message_metadata_model(self):
        """The WS wire type (pydantic, extra="allow") is the other metadata
        shape ``ExecutionContext._metadata_to_dict`` normalizes; the parser
        accepts it directly."""
        metadata = MessageMetadata.model_validate(
            {"mentions": [], "status": "sent", "delegation": make_envelope_dict()}
        )

        envelope = parse_delegation(metadata)

        assert isinstance(envelope, DelegationEnvelope)
        assert envelope.originator is not None
        assert envelope.originator.handle == "alice.asker"

    def test_none_metadata_returns_none(self):
        assert parse_delegation(None) is None

    def test_dict_metadata_without_envelope_returns_none(self):
        assert parse_delegation({"mentions": [], "status": "sent"}) is None

    def test_model_metadata_without_envelope_returns_none(self):
        assert parse_delegation(MessageMetadata(mentions=[], status="sent")) is None

    def test_accepts_an_already_parsed_envelope(self):
        envelope = DelegationEnvelope.model_validate(make_envelope_dict())
        assert parse_delegation({"delegation": envelope}) is envelope


class TestParseDelegationTolerance:
    """I3: a malformed envelope never raises — log and treat as absent."""

    def test_junk_string_envelope_returns_none(self):
        assert parse_delegation({"delegation": "junk"}) is None

    def test_junk_int_envelope_returns_none(self):
        assert parse_delegation({"delegation": 42}) is None

    def test_junk_list_envelope_returns_none(self):
        assert parse_delegation({"delegation": ["not", "an", "envelope"]}) is None

    def test_junk_version_returns_none(self):
        envelope = make_envelope_dict()
        envelope["version"] = "not-an-int"
        assert parse_delegation({"delegation": envelope}) is None

    def test_junk_originator_returns_none(self):
        envelope = make_envelope_dict()
        envelope["originator"] = "alice"
        assert parse_delegation({"delegation": envelope}) is None

    def test_junk_metadata_object_returns_none(self):
        """Even a metadata object that is neither dict nor model is absorbed."""
        assert parse_delegation(object()) is None
        assert parse_delegation("metadata?") is None

    def test_version_2_still_parses(self):
        """Version-tolerant by design: a newer platform's envelope is parsed,
        not dropped (M2.5 may bump the version)."""
        envelope = make_envelope_dict()
        envelope["version"] = 2

        parsed = parse_delegation({"delegation": envelope})

        assert parsed is not None
        assert parsed.version == 2

    def test_partial_envelope_parses(self):
        """A partially formed envelope is better than a dropped one — every
        field is optional."""
        parsed = parse_delegation({"delegation": {"originator": {"handle": "a.b"}}})

        assert parsed is not None
        assert parsed.version is None
        assert parsed.originator is not None
        assert parsed.originator.handle == "a.b"

    def test_unknown_keys_are_absorbed(self):
        """extra="allow" absorbs platform additions (e.g. a future hop shape)."""
        envelope = make_envelope_dict()
        envelope["hop"] = {"via": "org/router"}
        envelope["new_platform_key"] = "surprise"

        parsed = parse_delegation({"delegation": envelope})

        assert parsed is not None
        assert parsed.hop == {"via": "org/router"}
        assert parsed.model_extra is not None
        assert parsed.model_extra["new_platform_key"] == "surprise"


class TestParseDelegationValue:
    """The value-level coercion used at the wire boundary (MessageMetadata)."""

    def test_parses_a_dict_value(self):
        parsed = parse_delegation_value(make_envelope_dict())
        assert isinstance(parsed, DelegationEnvelope)
        assert parsed.message_id == ENVELOPE_MESSAGE_ID

    def test_passes_through_an_envelope_instance(self):
        envelope = DelegationEnvelope.model_validate(make_envelope_dict())
        assert parse_delegation_value(envelope) is envelope

    def test_none_returns_none(self):
        assert parse_delegation_value(None) is None

    def test_junk_returns_none_without_raising(self):
        assert parse_delegation_value("junk") is None
        assert parse_delegation_value(3.14) is None


class TestMessageMetadataWireBoundary:
    """The WS wire type carries the envelope as a typed field, and the typed
    field must stay tolerant: the REST backlog path re-wraps raw dicts with
    ``MessageMetadata(**metadata)`` (execution.py), so a malformed envelope
    must not fail the whole metadata parse (I3)."""

    def test_wire_metadata_types_the_envelope(self):
        metadata = MessageMetadata.model_validate(
            {"mentions": [], "status": "sent", "delegation": make_envelope_dict()}
        )

        assert isinstance(metadata.delegation, DelegationEnvelope)
        assert metadata.delegation.originator is not None
        assert metadata.delegation.originator.handle == "alice.asker"

    def test_absent_envelope_defaults_to_none(self):
        assert MessageMetadata(mentions=[], status="sent").delegation is None

    def test_rewrap_kwargs_path_tolerates_a_malformed_envelope(self):
        """Mirrors ExecutionContext._process_claimed_backlog_message, which
        constructs ``MessageMetadata(**metadata)`` from platform dicts."""
        metadata = MessageMetadata(
            **{"mentions": [], "status": "sent", "delegation": "junk"}
        )

        assert metadata.delegation is None

    def test_rewrap_kwargs_path_tolerates_junk_version(self):
        envelope = make_envelope_dict()
        envelope["version"] = "not-an-int"

        metadata = MessageMetadata(
            **{"mentions": [], "status": "sent", "delegation": envelope}
        )

        assert metadata.delegation is None

    def test_model_dump_then_rewrap_keeps_the_envelope(self):
        """The backlog cycle dumps the WS model to a dict and re-wraps it;
        the envelope must survive that round trip."""
        original = MessageMetadata.model_validate(
            {"mentions": [], "status": "sent", "delegation": make_envelope_dict()}
        )

        rewrapped = MessageMetadata(**original.model_dump())

        assert rewrapped.delegation is not None
        assert rewrapped.delegation.message_id == ENVELOPE_MESSAGE_ID
        assert rewrapped.delegation.originator is not None
        assert rewrapped.delegation.originator.uuid == ORIGINATOR_UUID


class TestEnvelopeIsReadOnly:
    """I2: handler code gets a read-only view — the envelope is platform-
    authored and nothing in the SDK writes through it."""

    def test_envelope_rejects_assignment(self):
        envelope = DelegationEnvelope.model_validate(make_envelope_dict())
        try:
            envelope.message_id = "overwritten"
        except Exception:
            pass
        else:
            raise AssertionError("frozen envelope accepted attribute assignment")
        assert envelope.message_id == ENVELOPE_MESSAGE_ID

    def test_originator_rejects_assignment(self):
        envelope = DelegationEnvelope.model_validate(make_envelope_dict())
        assert envelope.originator is not None
        try:
            envelope.originator.handle = "mallory"
        except Exception:
            pass
        else:
            raise AssertionError("frozen originator accepted attribute assignment")
        assert envelope.originator.handle == "alice.asker"
