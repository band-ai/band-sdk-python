"""The Codex adapter's bridge into the common Room lifecycle suite."""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.core.retention import RetainedRoom
from band.integrations.codex import CodexStdioClient
from tests.adapters.hold import Hold
from tests.adapters.lifecycle.kit import (
    LifecycleBridge,
    LifecycleProfile,
    RetentionHost,
    pause_at,
)
from tests.paths import REPO_ROOT

# Where the Codex peer saves each thread's conversation, under the host root.
CODEX_HOME = "codex-home"


class CodexPeer(CodexStdioClient):
    """Inject a held start or a failed or held close at the real transport."""

    refuse_close = False
    starting: Hold | None = None
    closing: Hold | None = None

    async def initialize(self, **kwargs: Any) -> dict[str, Any]:
        await pause_at(self.starting)
        return await super().initialize(**kwargs)

    async def _close_process(self) -> None:
        await pause_at(self.closing)
        if self.refuse_close:
            raise RuntimeError("subprocess cleanup failed")
        await super()._close_process()

    @property
    def exited(self) -> bool:
        return self._proc is not None and self._proc.returncode is not None


def install(host: RetentionHost, monkeypatch: pytest.MonkeyPatch) -> LifecycleBridge:
    adapter = CodexAdapter(
        CodexAdapterConfig(
            model="peer",
            workspace_for_room=host.workspace,
            codex_command=(
                sys.executable,
                "-u",
                str(REPO_ROOT / "tests/adapters/roompeer.py"),
                *host.peer_arguments,
                "--home",
                str(host.root / CODEX_HOME),
                "--stay",
            ),
        )
    )

    def child(
        *,
        command: Sequence[str] | None,
        cwd: str | None,
        env: Mapping[str, str] | None,
    ) -> CodexPeer:
        peer = CodexPeer(command=command, cwd=cwd, env=env)
        host.spawned(peer, cwd)
        return peer

    monkeypatch.setattr("band.adapters.codex.CodexStdioClient", child)

    def residency(room_id: str) -> RetainedRoom | None:
        room = adapter._room_clients.get(room_id)
        return room.residency if room is not None else None

    return LifecycleBridge(
        adapter=adapter, residency=residency, native_id=adapter._room_threads.get
    )


PROFILES = [
    LifecycleProfile(name="codex", install=install, lost_turn=TurnResultAlreadyReported)
]
