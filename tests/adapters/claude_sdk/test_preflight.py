"""Standalone Claude handshakes own their real subprocess through cleanup."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.claude_sdk import (
    ClaudeCLIOptions,
    ClaudeSDKAdapter,
    ClaudeSDKAdapterConfig,
)
from band.core.exceptions import CleanupIncompleteError
from tests.adapters.claude_sdk.hostprobe import HostProbeProcess, host_probe
from tests.adapters.claude_sdk.probepeer import PRIVATE_ERROR, ProbeMode


async def test_configured_handshake_creates_no_room_and_reaps_its_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    probe = host_probe(monkeypatch, tmp_path)
    try:
        result = await probe.adapter.preflight(timeout_s=5)
        assert result.ok
        [child] = probe.children
        assert child.exited
        [frame] = probe.frames()
        assert frame["message"]["request"]["subtype"] == "initialize"
        assert frame["cwd"] == str(tmp_path.resolve())
        assert frame["marker"] == "configured-child-environment"
        assert frame["setting_sources"] == ["user", "project"]
        assert probe.adapter._session_manager is None
        assert probe.adapter._mcp.current is None
        assert probe.adapter._workspaces is None
    finally:
        await probe.adapter.cleanup_all()


@pytest.mark.parametrize("mode", [ProbeMode.ERROR, ProbeMode.EOF])
async def test_failed_handshake_closes_the_process_without_echoing_provider_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: ProbeMode
) -> None:
    probe = host_probe(monkeypatch, tmp_path, mode=mode)
    try:
        result = await probe.adapter.preflight(timeout_s=5)
        assert not result.ok
        assert PRIVATE_ERROR not in str(result)
        [child] = probe.children
        assert child.exited
        [frame] = probe.frames()
        assert frame["message"]["request"]["subtype"] == "initialize"
    finally:
        await probe.adapter.cleanup_all()


async def test_timeout_keeps_a_process_owned_until_delayed_close_finishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    close_gate = asyncio.Event()

    def hold_close(child: HostProbeProcess) -> None:
        child.close_gate = close_gate

    probe = host_probe(monkeypatch, tmp_path, mode=ProbeMode.HOLD, on_launch=hold_close)
    try:
        with pytest.raises(CleanupIncompleteError):
            await probe.adapter.preflight(timeout_s=0.5)
        [child] = probe.children
        assert not child.exited
        [frame] = probe.frames()
        assert frame["message"]["request"]["subtype"] == "initialize"
        close_gate.set()
        await probe.adapter.cleanup_all()
        assert child.exited
    finally:
        close_gate.set()
        await probe.adapter.cleanup_all()


async def test_configured_missing_cli_is_not_replaced_by_a_bundled_default(
    tmp_path: Path,
) -> None:
    adapter = ClaudeSDKAdapter(
        ClaudeSDKAdapterConfig(
            cli=ClaudeCLIOptions(cli_path=str(tmp_path / "missing-claude"))
        )
    )
    try:
        result = await adapter.preflight(timeout_s=5)
        assert not result.ok
    finally:
        await adapter.cleanup_all()


async def test_failed_probe_close_remains_retryable_and_does_not_skip_mcp_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refuse(child: HostProbeProcess) -> None:
        child.refuse_close = True

    probe = host_probe(monkeypatch, tmp_path, on_launch=refuse)
    try:
        await probe.adapter.on_started("Host", "Standalone probe owner")
        assert probe.adapter._mcp.current is not None
        with pytest.raises(CleanupIncompleteError):
            await probe.adapter.preflight(timeout_s=5)
        [child] = probe.children
        assert not child.exited
        with pytest.raises(CleanupIncompleteError):
            await probe.adapter.cleanup_all()
        assert probe.adapter._mcp.current is None
        assert not child.exited
        child.refuse_close = False
        await probe.adapter.cleanup_all()
        assert child.exited
    finally:
        for child in probe.children:
            child.refuse_close = False
        await probe.adapter.cleanup_all()
