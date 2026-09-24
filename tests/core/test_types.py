"""Tests for Capability, Emit enums and AdapterFeatures dataclass."""

from __future__ import annotations

import pytest
from band_rest.types.chat_message_metadata import ChatMessageMetadata

from band.client.streaming import MessageMetadata
from band.core.types import AdapterFeatures, Capability, Emit, metadata_to_dict


class TestCapabilityEnum:
    def test_members_combine_with_or_into_a_frozenset(self) -> None:
        combined = Capability.MEMORY | Capability.CONTACTS
        assert combined == frozenset({Capability.MEMORY, Capability.CONTACTS})


class TestEmitEnum:
    def test_members_combine_with_or_into_a_frozenset(self) -> None:
        combined = Emit.TOOL_CALLS | Emit.THOUGHTS | Emit.USAGE
        assert combined == frozenset({Emit.TOOL_CALLS, Emit.THOUGHTS, Emit.USAGE})


class TestAdapterFeatures:
    def test_iterable_inputs_normalized_to_frozen_types(self) -> None:
        """Callers pass sets/lists; the container stores frozenset/tuple."""
        f = AdapterFeatures(
            capabilities=[Capability.MEMORY, Capability.CONTACTS],
            emit={Emit.TOOL_CALLS, Emit.THOUGHTS},
            include_tools=["band_send_message", "band_lookup_peers"],
            exclude_tools=["band_store_memory"],
            include_categories=["chat", "memory"],
        )
        assert f.capabilities == frozenset({Capability.MEMORY, Capability.CONTACTS})
        assert f.emit == frozenset({Emit.TOOL_CALLS, Emit.THOUGHTS})
        assert f.include_tools == ("band_send_message", "band_lookup_peers")
        assert f.exclude_tools == ("band_store_memory",)
        assert f.include_categories == ("chat", "memory")

    def test_frozen_raises_on_assignment(self) -> None:
        f = AdapterFeatures()
        with pytest.raises(AttributeError):
            f.capabilities = frozenset({Capability.MEMORY})  # type: ignore[misc]


class TestMetadataToDict:
    """Normalizes dict-or-Pydantic-model metadata (band-client-rest 0.0.38
    made ``ChatMessage.metadata`` a frozen, ``.get()``-less model)."""

    def test_dict_passes_through_unchanged(self) -> None:
        original = {"a": 1}
        assert metadata_to_dict(original) is original

    @pytest.mark.parametrize("bad", [None, "nope", 42])
    def test_non_dict_non_model_becomes_empty_dict(self, bad: object) -> None:
        assert metadata_to_dict(bad) == {}

    def test_default_dump_keeps_none_valued_status_key(self) -> None:
        """ExecutionContext fills a missing ``status`` key with ``"sent"`` —
        a default dump must keep an explicitly-unset status distinguishable
        from an absent one."""
        dumped = metadata_to_dict(MessageMetadata(status=None))
        assert "status" in dumped
        assert dumped["status"] is None

    def test_exclude_none_keeps_extras_and_omits_none_fields(self) -> None:
        model = ChatMessageMetadata(
            delegation=None,
            band_usage={"input_tokens": 1},
            claude_sdk_session_id="sess-1",
        )
        dumped = metadata_to_dict(model, exclude_none=True)
        assert dumped["band_usage"] == {"input_tokens": 1}
        assert dumped["claude_sdk_session_id"] == "sess-1"
        assert "delegation" not in dumped
