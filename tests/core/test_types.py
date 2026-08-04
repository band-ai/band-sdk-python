"""Tests for Capability, Emit enums and AdapterFeatures dataclass."""

from __future__ import annotations

import pytest

from band.core.types import AdapterFeatures, Capability, Emit


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
