"""The Codex adapter's bridge into the common Room lifecycle suite."""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from functools import partial
from typing import Any

import pytest

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.codex import CodexJsonRpcError, CodexStdioClient
from band.integrations.codex.mcp import CodexMcpRedactor
from tests.adapters.codex.kit import residency_of, thread_of
from tests.adapters.hold import Hold
from tests.adapters.lifecycle.kit import (
    ExpectedFailure,
    LifecycleBridge,
    LifecycleProfile,
    RetentionHost,
    pause_at,
)
from tests.adapters.roompeer import CLEANUP_FAILURE, CODEX_HOME, STARTUP_FAILURE
from tests.paths import ROOM_PEER


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
            raise RuntimeError(CLEANUP_FAILURE)
        await super()._close_process()

    @property
    def exited(self) -> bool:
        return self._proc is not None and self._proc.returncode is not None


def install(
    host: RetentionHost,
    monkeypatch: pytest.MonkeyPatch,
    **settings: Any,
) -> LifecycleBridge:
    """``settings`` override the production config's fields."""
    adapter = CodexAdapter(
        CodexAdapterConfig(
            model="peer",
            workspace_for_room=host.workspace,
            codex_command=(
                sys.executable,
                "-u",
                str(ROOM_PEER),
                *host.peer_arguments,
                "--home",
                str(host.root / CODEX_HOME),
                "--stay",
            ),
            **settings,
        )
    )

    def child(
        *,
        command: Sequence[str] | None,
        cwd: str | None,
        env: Mapping[str, str] | None,
        redactor: CodexMcpRedactor | None,
    ) -> CodexPeer:
        peer = CodexPeer(command=command, cwd=cwd, env=env, redactor=redactor)
        host.spawned(peer, cwd)
        return peer

    monkeypatch.setattr("band.adapters.codex.CodexStdioClient", child)

    return LifecycleBridge(
        adapter=adapter,
        residency=partial(residency_of, adapter),
        native_id=partial(thread_of, adapter),
    )


PROFILES = [
    LifecycleProfile(
        name="codex",
        install=install,
        lost_turn=TurnResultAlreadyReported,
        failed_start=ExpectedFailure(CodexJsonRpcError, STARTUP_FAILURE),
    )
]
