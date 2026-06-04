"""Tests for Capability, Emit enums and AdapterFeatures dataclass."""

from __future__ import annotations

import pytest

from band.core.types import AdapterFeatures, Capability, Emit


class TestCapabilityEnum:
    def test_values(self) -> None:
        assert Capability.MEMORY == "memory"
        assert Capability.CONTACTS == "contacts"

    def test_is_str_enum(self) -> None:
        assert isinstance(Capability.MEMORY, str)


class TestEmitEnum:
    def test_values(self) -> None:
        assert Emit.EXECUTION == "execution"
        assert Emit.THOUGHTS == "thoughts"
        assert Emit.TASK_EVENTS == "task_events"

    def test_is_str_enum(self) -> None:
        assert isinstance(Emit.EXECUTION, str)


class TestAdapterFeatures:
    def test_empty_defaults(self) -> None:
        f = AdapterFeatures()
        assert f.capabilities == frozenset()
        assert f.emit == frozenset()
        assert f.include_tools is None
        assert f.exclude_tools is None
        assert f.include_categories is None

    def test_set_literal_normalized_to_frozenset(self) -> None:
        f = AdapterFeatures(
            capabilities={Capability.MEMORY},
            emit={Emit.EXECUTION, Emit.THOUGHTS},
        )
        assert isinstance(f.capabilities, frozenset)
        assert isinstance(f.emit, frozenset)
        assert Capability.MEMORY in f.capabilities
        assert Emit.EXECUTION in f.emit
        assert Emit.THOUGHTS in f.emit

    def test_list_normalized_to_frozenset(self) -> None:
        f = AdapterFeatures(
            capabilities=[Capability.MEMORY, Capability.CONTACTS],
        )
        assert isinstance(f.capabilities, frozenset)
        assert len(f.capabilities) == 2

    def test_include_tools_normalized_to_tuple(self) -> None:
        f = AdapterFeatures(include_tools=["band_send_message", "band_lookup_peers"])
        assert isinstance(f.include_tools, tuple)
        assert f.include_tools == ("band_send_message", "band_lookup_peers")

    def test_exclude_tools_normalized_to_tuple(self) -> None:
        f = AdapterFeatures(exclude_tools=["band_store_memory"])
        assert isinstance(f.exclude_tools, tuple)

    def test_include_categories_normalized_to_tuple(self) -> None:
        f = AdapterFeatures(include_categories=["chat", "memory"])
        assert isinstance(f.include_categories, tuple)
        assert f.include_categories == ("chat", "memory")

    def test_frozen_raises_on_assignment(self) -> None:
        f = AdapterFeatures()
        with pytest.raises(AttributeError):
            f.capabilities = frozenset({Capability.MEMORY})  # type: ignore[misc]

    def test_hashable(self) -> None:
        f1 = AdapterFeatures(capabilities={Capability.MEMORY})
        f2 = AdapterFeatures(capabilities={Capability.MEMORY})
        assert hash(f1) == hash(f2)
        assert f1 == f2

    def test_different_features_not_equal(self) -> None:
        f1 = AdapterFeatures(capabilities={Capability.MEMORY})
        f2 = AdapterFeatures(capabilities={Capability.CONTACTS})
        assert f1 != f2
