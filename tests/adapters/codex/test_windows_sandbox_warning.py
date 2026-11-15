"""Codex adapter: a Windows app-server with no sandbox backend is reported once, with the fix."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from band.adapters.codex import (
    WINDOWS_SANDBOX_MISSING_WARNING,
    WINDOWS_SANDBOX_SETUP_WARNING,
    CodexSandboxMode,
    CodexWindowsSandbox,
)
from band.integrations.codex import CodexJsonRpcError
from band.integrations.codex.types import (
    WINDOWS_PLATFORM_OS,
    CodexRequestMethod,
    CodexWindowsSandboxReadiness,
)
from tests.adapters.codex.kit import StartCodex, ToolSchemaFakeTools, send_bootstrap
from tests.adapters.codexturns import FakeCodexClient, turn_completed

ADAPTER_LOGGER = "band.adapters.codex"


def logged_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """The adapter's warning messages, in order."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == ADAPTER_LOGGER and record.levelno == logging.WARNING
    ]


def readiness_requests(client: FakeCodexClient) -> int:
    return client.request_methods.count(CodexRequestMethod.WINDOWS_SANDBOX_READINESS)


class TestWindowsSandboxWarning:
    """Only a sandbox Codex must enforce on Windows asks readiness, once per adapter."""

    @pytest.mark.parametrize(
        ("config", "readiness", "warnings"),
        [
            (
                {"sandbox": CodexSandboxMode.WORKSPACE_WRITE},
                CodexWindowsSandboxReadiness.NOT_CONFIGURED,
                [WINDOWS_SANDBOX_MISSING_WARNING],
            ),
            (
                {"sandbox": CodexSandboxMode.READ_ONLY},
                CodexWindowsSandboxReadiness.NOT_CONFIGURED,
                [WINDOWS_SANDBOX_MISSING_WARNING],
            ),
            (
                {"sandbox_policy": {"type": "workspaceWrite"}},
                CodexWindowsSandboxReadiness.NOT_CONFIGURED,
                [WINDOWS_SANDBOX_MISSING_WARNING],
            ),
            (
                {},
                CodexWindowsSandboxReadiness.NOT_CONFIGURED,
                [WINDOWS_SANDBOX_MISSING_WARNING],
            ),
            (
                {"sandbox": CodexSandboxMode.WORKSPACE_WRITE},
                CodexWindowsSandboxReadiness.UPDATE_REQUIRED,
                [WINDOWS_SANDBOX_SETUP_WARNING],
            ),
            (
                {"sandbox": CodexSandboxMode.WORKSPACE_WRITE},
                CodexWindowsSandboxReadiness.READY,
                [],
            ),
            (
                {"sandbox": CodexSandboxMode.WORKSPACE_WRITE},
                CodexJsonRpcError(code=-32601, message="Method not found"),
                [],
            ),
        ],
        ids=[
            "workspace-write-not-configured",
            "read-only-not-configured",
            "policy-not-configured",
            "codex-default-not-configured",
            "elevated-setup-unfinished",
            "ready",
            "readiness-unsupported",
        ],
    )
    async def test_windows_readiness_decides_the_warning(
        self,
        started_codex: StartCodex,
        caplog: pytest.LogCaptureFixture,
        config: dict[str, Any],
        readiness: str | Exception,
        warnings: list[str],
    ) -> None:
        client = FakeCodexClient(
            events=[turn_completed()],
            platform_os=WINDOWS_PLATFORM_OS,
            windows_sandbox_readiness=readiness,
        )
        adapter = await started_codex(client, **config)

        with caplog.at_level(logging.WARNING, logger=ADAPTER_LOGGER):
            await send_bootstrap(adapter, tools=ToolSchemaFakeTools())

        assert (readiness_requests(client), logged_warnings(caplog)) == (1, warnings)
        assert client.params_of(CodexRequestMethod.TURN_START) != []

    @pytest.mark.parametrize(
        ("platform_os", "config"),
        [
            (WINDOWS_PLATFORM_OS, {"sandbox": CodexSandboxMode.DANGER_FULL_ACCESS}),
            (WINDOWS_PLATFORM_OS, {"sandbox": "external-sandbox"}),
            (
                WINDOWS_PLATFORM_OS,
                {
                    "sandbox": CodexSandboxMode.WORKSPACE_WRITE,
                    "windows_sandbox": CodexWindowsSandbox.UNELEVATED,
                },
            ),
            ("linux", {"sandbox": CodexSandboxMode.WORKSPACE_WRITE}),
            (None, {"sandbox": CodexSandboxMode.WORKSPACE_WRITE}),
        ],
        ids=[
            "full-access",
            "external-sandbox",
            "backend-configured",
            "linux",
            "platform-unreported",
        ],
    )
    async def test_no_windows_backend_needed_skips_readiness(
        self,
        started_codex: StartCodex,
        caplog: pytest.LogCaptureFixture,
        platform_os: str | None,
        config: dict[str, Any],
    ) -> None:
        client = FakeCodexClient(events=[turn_completed()], platform_os=platform_os)
        adapter = await started_codex(client, **config)

        with caplog.at_level(logging.WARNING, logger=ADAPTER_LOGGER):
            await send_bootstrap(adapter, tools=ToolSchemaFakeTools())

        assert (readiness_requests(client), logged_warnings(caplog)) == (0, [])
        assert client.params_of(CodexRequestMethod.TURN_START) != []

    async def test_rooms_sharing_an_adapter_are_warned_once(
        self, started_codex: StartCodex, caplog: pytest.LogCaptureFixture
    ) -> None:
        first, second = (
            FakeCodexClient(
                events=[turn_completed()],
                platform_os=WINDOWS_PLATFORM_OS,
                windows_sandbox_readiness=CodexWindowsSandboxReadiness.NOT_CONFIGURED,
            )
            for _ in range(2)
        )
        adapter = await started_codex(
            first, second, sandbox=CodexSandboxMode.WORKSPACE_WRITE
        )

        with caplog.at_level(logging.WARNING, logger=ADAPTER_LOGGER):
            await send_bootstrap(adapter, room_id="room-a", tools=ToolSchemaFakeTools())
            await send_bootstrap(adapter, room_id="room-b", tools=ToolSchemaFakeTools())

        assert (
            readiness_requests(first) + readiness_requests(second),
            logged_warnings(caplog),
        ) == (1, [WINDOWS_SANDBOX_MISSING_WARNING])
        assert second.params_of(CodexRequestMethod.TURN_START) != []
