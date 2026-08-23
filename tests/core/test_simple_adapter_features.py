"""Tests for SimpleAdapter's flattened feature kwargs (emit, capabilities, ...)."""

from __future__ import annotations

from typing import Any

import pytest

from band.core.exceptions import BandConfigError
from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import Capability, Emit, PlatformMessage


class _TestAdapter(SimpleAdapter[list[Any]]):
    """Minimal concrete adapter for testing."""

    SUPPORTED_EMIT = frozenset({Emit.TOOL_CALLS, Emit.THOUGHTS})
    SUPPORTED_CAPABILITIES = frozenset({Capability.MEMORY})

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: list[Any],
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        pass


class _BareAdapter(SimpleAdapter[list[Any]]):
    """Adapter that declares no SUPPORTED_* (like a direct FrameworkAdapter impl)."""

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: list[Any],
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        pass


class TestEmitDefaultsToEverythingSupported:
    def test_omitted_emit_defaults_to_supported_emit(self) -> None:
        adapter = _TestAdapter()
        assert adapter.features.emit == _TestAdapter.SUPPORTED_EMIT

    def test_bare_adapter_defaults_to_empty_emit(self) -> None:
        adapter = _BareAdapter()
        assert adapter.features.emit == frozenset()

    def test_explicit_empty_tuple_silences_emit(self) -> None:
        adapter = _TestAdapter(emit=())
        assert adapter.features.emit == frozenset()


class TestCapabilitiesDefaultToEmpty:
    def test_omitted_capabilities_defaults_to_empty(self) -> None:
        adapter = _TestAdapter()
        assert adapter.features.capabilities == frozenset()


class TestFlagInputNormalization:
    def test_single_emit_member_is_wrapped(self) -> None:
        adapter = _TestAdapter(emit=Emit.THOUGHTS)
        assert adapter.features.emit == frozenset({Emit.THOUGHTS})

    def test_single_capability_member_is_wrapped(self) -> None:
        adapter = _TestAdapter(capabilities=Capability.MEMORY)
        assert adapter.features.capabilities == frozenset({Capability.MEMORY})

    def test_or_combined_emit_members_accepted(self) -> None:
        combined = Emit.TOOL_CALLS | Emit.THOUGHTS
        adapter = _TestAdapter(emit=combined)
        assert adapter.features.emit == frozenset({Emit.TOOL_CALLS, Emit.THOUGHTS})

    def test_set_literal_still_accepted(self) -> None:
        adapter = _TestAdapter(emit={Emit.TOOL_CALLS})
        assert adapter.features.emit == frozenset({Emit.TOOL_CALLS})


class TestToolFilterPassthrough:
    def test_include_exclude_categories_land_on_features(self) -> None:
        adapter = _TestAdapter(
            include_tools=["band_send_message", "band_lookup_peers"],
            exclude_tools={"band_remove_participant"},
            include_categories=("chat", "memory"),
        )
        assert adapter.features.include_tools == (
            "band_send_message",
            "band_lookup_peers",
        )
        assert adapter.features.exclude_tools == ("band_remove_participant",)
        assert adapter.features.include_categories == ("chat", "memory")


class TestConstructionTimeValidation:
    def test_unsupported_emit_raises_immediately(self) -> None:
        with pytest.raises(BandConfigError, match="does not support emit"):
            _TestAdapter(emit=Emit.USAGE)

    def test_unsupported_capability_raises_immediately(self) -> None:
        with pytest.raises(BandConfigError, match="does not support capabilit"):
            _TestAdapter(capabilities=Capability.CONTACTS)

    def test_bare_adapter_rejects_any_emit(self) -> None:
        """An adapter with no SUPPORTED_EMIT rejects any explicit emit request."""
        with pytest.raises(BandConfigError, match="does not support emit"):
            _BareAdapter(emit=Emit.TOOL_CALLS)

    def test_supported_values_do_not_raise(self) -> None:
        adapter = _TestAdapter(emit=Emit.TOOL_CALLS, capabilities=Capability.MEMORY)
        assert adapter.features.emit == frozenset({Emit.TOOL_CALLS})
        assert adapter.features.capabilities == frozenset({Capability.MEMORY})
