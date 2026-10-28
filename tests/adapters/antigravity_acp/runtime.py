"""Own a real stdio peer adapter and its isolated test files."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from band.adapters.antigravity_acp import (
    AntigravityACPAdapter,
    AntigravityACPAdapterConfig,
)
from band.testing.platform import platform_connection_stub
from tests.adapters.antigravity_acp.kit import AntigravityRoom
from tests.adapters.antigravity_acp.peerconfig import PeerSettings
from tests.paths import REPO_ROOT


@asynccontextmanager
async def open_room(
    root: Path,
    settings: PeerSettings | None = None,
    *,
    agent_id: str = "agent-antigravity",
    config: dict[str, Any] | None = None,
    **adapter_options: Any,
) -> AsyncIterator[AntigravityRoom]:
    root.mkdir(parents=True, exist_ok=True)
    journal_root = root / "journal"
    script = root / "peer.json"
    settings = (settings or PeerSettings()).model_copy(
        update={"journal_root": str(journal_root)}
    )
    script.write_text(settings.model_dump_json())
    values = {
        "command": (
            sys._base_executable,
            "-m",
            "tests.adapters.antigravity_acp.peer",
            str(script),
        ),
        "gemini_home": str(root / "native"),
        "cwd": str(root / "workspaces"),
        **(config or {}),
    }
    values["env"] = {
        "__PYVENV_LAUNCHER__": sys.executable,
        "PYTHONPATH": str(REPO_ROOT),
        **(values.get("env") or {}),
    }
    adapter = AntigravityACPAdapter(
        AntigravityACPAdapterConfig(**values), **adapter_options
    )
    adapter.platform = platform_connection_stub(agent_id)
    try:
        await adapter.on_started("Antigravity", "A test agent")
        yield AntigravityRoom(adapter, journal_root)
    finally:
        await adapter.cleanup_all()
