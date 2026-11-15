"""Codex adapter: a configured Windows sandbox backend reaches every thread it starts or resumes."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from band.adapters.codex import CodexSandboxMode, CodexWindowsSandbox
from band.integrations.codex.types import (
    WINDOWS_SANDBOX_CONFIG_KEY,
    CodexRequestMethod,
    CodexSessionState,
)
from band.workspaces import create_room_workspace_resolver
from tests.adapters.codex.kit import (
    ROOM_ID,
    StartCodex,
    ToolSchemaFakeTools,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FIRST_THREAD_ID,
    LISTED_MODEL,
    FakeCodexClient,
    turn_completed,
)


class TestWindowsSandboxOverride:
    """``windows_sandbox`` rides each thread's config; unset leaves Codex's own."""

    @pytest.mark.parametrize(
        ("windows_sandbox", "thread_config"),
        [
            (
                CodexWindowsSandbox.UNELEVATED,
                {"config": {WINDOWS_SANDBOX_CONFIG_KEY: "unelevated"}},
            ),
            (
                CodexWindowsSandbox.ELEVATED,
                {"config": {WINDOWS_SANDBOX_CONFIG_KEY: "elevated"}},
            ),
            (None, {}),
        ],
        ids=["unelevated", "elevated", "codex-config"],
    )
    async def test_room_thread_runs_in_its_workspace_with_the_backend(
        self,
        started_codex: StartCodex,
        tmp_path: Path,
        windows_sandbox: CodexWindowsSandbox | None,
        thread_config: dict[str, Any],
    ) -> None:
        resolver = create_room_workspace_resolver(tmp_path)
        client = FakeCodexClient(events=[turn_completed()])
        adapter = await started_codex(
            client,
            sandbox=CodexSandboxMode.WORKSPACE_WRITE,
            windows_sandbox=windows_sandbox,
            workspace_for_room=resolver,
        )

        await send_bootstrap(adapter, tools=ToolSchemaFakeTools())

        workspace = resolver(ROOM_ID)
        assert client.params_of(CodexRequestMethod.THREAD_START) == [
            {
                "model": LISTED_MODEL,
                "cwd": workspace,
                "approvalPolicy": "never",
                "personality": "pragmatic",
                "dynamicTools": mock.ANY,
                "sandbox": "workspace-write",
                **thread_config,
            }
        ]
        assert client.params_of(CodexRequestMethod.TURN_START) == [
            {
                "threadId": FIRST_THREAD_ID,
                "input": mock.ANY,
                "model": LISTED_MODEL,
                "cwd": workspace,
                "approvalPolicy": "never",
                "personality": "pragmatic",
                "sandboxPolicy": {"type": "workspaceWrite"},
            }
        ]

    async def test_resumed_thread_keeps_the_backend(
        self, started_codex: StartCodex, tmp_path: Path
    ) -> None:
        resolver = create_room_workspace_resolver(tmp_path)
        client = FakeCodexClient(events=[turn_completed()])
        adapter = await started_codex(
            client,
            sandbox=CodexSandboxMode.WORKSPACE_WRITE,
            windows_sandbox=CodexWindowsSandbox.UNELEVATED,
            workspace_for_room=resolver,
        )

        await send_bootstrap(
            adapter,
            tools=ToolSchemaFakeTools(),
            history=CodexSessionState(room_to_thread={ROOM_ID: "thr-history"}),
        )

        assert client.params_of(CodexRequestMethod.THREAD_RESUME) == [
            {
                "threadId": "thr-history",
                "cwd": resolver(ROOM_ID),
                "personality": "pragmatic",
                "sandbox": "workspace-write",
                "config": {WINDOWS_SANDBOX_CONFIG_KEY: "unelevated"},
            }
        ]
