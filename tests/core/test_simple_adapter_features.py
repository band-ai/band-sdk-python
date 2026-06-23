"""Tests for SimpleAdapter features param and unsupported-value warnings."""

from __future__ import annotations

import logging
import warnings
from typing import Any

import pytest

from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AdapterFeatures, Capability, Emit, PlatformMessage


class _TestAdapter(SimpleAdapter[list[Any]]):
    """Minimal concrete adapter for testing."""

    SUPPORTED_EMIT = frozenset({Emit.EXECUTION})
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


class TestSimpleAdapterFeatures:
    def test_defaults_to_empty_features(self) -> None:
        adapter = _TestAdapter()
        assert adapter.features == AdapterFeatures()

    def test_accepts_features_param(self) -> None:
        f = AdapterFeatures(
            capabilities={Capability.MEMORY},
            emit={Emit.EXECUTION},
        )
        adapter = _TestAdapter(features=f)
        assert adapter.features is f

    def test_adapter_features_normalizes_iterable_inputs(self) -> None:
        features = AdapterFeatures(
            capabilities=[Capability.MEMORY],
            emit=[Emit.EXECUTION],
            include_tools=["band_send_message", "band_lookup_peers"],
            exclude_tools={"band_remove_participant"},
            include_categories=("chat", "memory"),
        )

        assert features.capabilities == frozenset({Capability.MEMORY})
        assert features.emit == frozenset({Emit.EXECUTION})
        assert features.include_tools == (
            "band_send_message",
            "band_lookup_peers",
        )
        assert features.exclude_tools == ("band_remove_participant",)
        assert features.include_categories == ("chat", "memory")

    @pytest.mark.asyncio
    async def test_warns_on_unsupported_emit(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        adapter = _TestAdapter(
            features=AdapterFeatures(emit={Emit.EXECUTION, Emit.THOUGHTS}),
        )
        with caplog.at_level(logging.WARNING):
            with pytest.warns(UserWarning, match="does not support emit values"):
                await adapter.on_started("test-agent", "A test agent")
        assert "does not support emit values" in caplog.text
        assert "THOUGHTS" in caplog.text or "thoughts" in caplog.text

    @pytest.mark.asyncio
    async def test_warns_on_unsupported_capabilities(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        adapter = _TestAdapter(
            features=AdapterFeatures(
                capabilities={Capability.MEMORY, Capability.CONTACTS}
            ),
        )
        with caplog.at_level(logging.WARNING):
            with pytest.warns(UserWarning, match="does not support capability values"):
                await adapter.on_started("test-agent", "A test agent")
        assert "does not support capability values" in caplog.text
        assert "CONTACTS" in caplog.text or "contacts" in caplog.text

    @pytest.mark.asyncio
    async def test_no_warning_when_supported(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        adapter = _TestAdapter(
            features=AdapterFeatures(
                capabilities={Capability.MEMORY}, emit={Emit.EXECUTION}
            ),
        )
        with caplog.at_level(logging.WARNING):
            with warnings.catch_warnings():
                warnings.simplefilter("error", UserWarning)
                await adapter.on_started("test-agent", "A test agent")
        assert "does not support" not in caplog.text

    @pytest.mark.asyncio
    async def test_no_warning_on_empty_features(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        adapter = _TestAdapter()
        with caplog.at_level(logging.WARNING):
            with warnings.catch_warnings():
                warnings.simplefilter("error", UserWarning)
                await adapter.on_started("test-agent", "A test agent")
        assert "does not support" not in caplog.text

    @pytest.mark.asyncio
    async def test_bare_adapter_no_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Adapter with no SUPPORTED_* declarations warns on any non-empty features."""
        adapter = _BareAdapter(
            features=AdapterFeatures(emit={Emit.EXECUTION}),
        )
        with caplog.at_level(logging.WARNING):
            with pytest.warns(UserWarning, match="does not support emit values"):
                await adapter.on_started("test-agent", "A test agent")
        # _BareAdapter has empty SUPPORTED_EMIT, so EXECUTION is unsupported
        assert "does not support emit values" in caplog.text
