"""Codex adapter: enabling both turn-task channels warns operators once at startup."""

from __future__ import annotations

import logging

import pytest

from band.adapters.codex import CodexAdapterConfig
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
)


class TestDoubleEmitStartupWarning:
    """Enabling both turn-task channels warns operators once at startup."""

    @pytest.mark.asyncio
    async def test_warns_when_both_channels_enabled(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(
                emit_turn_task_markers=True, emit_turn_lifecycle_events=True
            ),
        )
        with caplog.at_level(logging.WARNING, logger="band.adapters.codex"):
            await adapter.on_started("Agent", "A coding agent")
        assert any(
            "two task events per turn" in record.message for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_no_warning_when_only_one_channel_enabled(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(
                emit_turn_task_markers=True, emit_turn_lifecycle_events=False
            ),
        )
        with caplog.at_level(logging.WARNING, logger="band.adapters.codex"):
            await adapter.on_started("Agent", "A coding agent")
        assert not any(
            "two task events per turn" in record.message for record in caplog.records
        )
