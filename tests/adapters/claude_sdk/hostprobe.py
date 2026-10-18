"""Real SDK spawning and control messages for standalone host probes."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from band.adapters.claude_sdk import (
    ClaudeCLIOptions,
    ClaudeSDKAdapter,
    ClaudeSDKAdapterConfig,
)
from tests.adapters.claude_sdk import probepeer
from tests.adapters.claude_sdk.probepeer import ProbeEnv, ProbeMode
from tests.adapters.claude_sdk.process import WorkspaceProcess, launch_children


class HostProbeProcess(WorkspaceProcess):
    close_gate: asyncio.Event | None = None

    async def close(self) -> None:
        if self.close_gate is not None:
            await self.close_gate.wait()
        await super().close()

    def _build_command(self) -> list[str]:
        sdk_command = SubprocessCLITransport._build_command(self)
        return [sys.executable, "-u", str(Path(probepeer.__file__)), *sdk_command[1:]]


@dataclass
class HostProbe:
    adapter: ClaudeSDKAdapter
    children: list[HostProbeProcess]
    receipt: Path

    def frames(self) -> list[dict[str, Any]]:
        return [
            json.loads(line)
            for line in self.receipt.read_text(encoding="utf-8").splitlines()
        ]


def host_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    mode: ProbeMode = ProbeMode.SUCCESS,
    info: dict[str, Any] | None = None,
    on_launch: Callable[[HostProbeProcess], None] | None = None,
) -> HostProbe:
    receipt = tmp_path / "requests.jsonl"
    config = ClaudeSDKAdapterConfig(
        cwd=tmp_path,
        setting_sources=("user", "project"),
        cli=ClaudeCLIOptions(
            cli_path=sys.executable,
            env={
                ProbeEnv.MODE: mode,
                ProbeEnv.RECEIPT: str(receipt),
                ProbeEnv.INFO: json.dumps(info if info is not None else {}),
                ProbeEnv.MARKER: "configured-child-environment",
            },
        ),
    )
    children = launch_children(monkeypatch, HostProbeProcess, on_launch)
    return HostProbe(ClaudeSDKAdapter(config), children, receipt)
