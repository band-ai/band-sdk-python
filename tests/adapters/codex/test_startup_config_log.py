"""Codex adapter: the startup config summary log."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapterConfig
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
)


class TestStartupConfigLog:
    @pytest.mark.asyncio
    async def test_startup_config_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Startup emits a redacted config summary log line."""
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(
                model="gpt-5.5", sandbox="workspace-write", approval_mode="manual"
            ),
        )

        with caplog.at_level("INFO", logger="band.adapters.codex"):
            await adapter.on_started("TestBot", "A test agent")

        startup_logs = [
            r for r in caplog.records if "Codex adapter started" in r.message
        ]
        assert len(startup_logs) == 1
        log_msg = startup_logs[0].message
        assert "agent=TestBot" in log_msg
        assert "transport=stdio" in log_msg
        assert "model=gpt-5.5" in log_msg
        assert "sandbox=workspace-write" in log_msg
        assert "approval_mode=manual" in log_msg
