"""Kit: stdio crash-report helpers shared by the ACP runtime tests."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest

from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from band.integrations.acp.client_adapter import ACPClientAdapter
from band.integrations.acp.client_runtime import ACPRuntime
from band.integrations.acp.stderr import STDERR_LINE_LOG_TEMPLATE
from tests.integrations.acp.acp_toolkit.harness import TranscriptTools
from tests.integrations.acp.acp_toolkit.peer import (
    INITIALIZE_PENDING_LINE,
    PEER_EXIT_CODE,
    ExitStage,
)

RUNTIME_LOGGER = "band.integrations.acp.client_runtime"
StdioRuntimeFactory = Callable[
    [ExitStage, list[str]], AbstractAsyncContextManager[ACPRuntime]
]


def runtime_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == RUNTIME_LOGGER and record.levelno == logging.WARNING
    ]


def assert_crash_report(caplog: pytest.LogCaptureFixture, lines: list[str]) -> None:
    [warning] = runtime_warnings(caplog)
    assert warning.getMessage() == (
        f"ACP agent exited with code {PEER_EXIT_CODE}; stderr tail:\n"
        + "\n".join(lines)
    )


async def deliver_stdio_turn(
    adapter: ACPClientAdapter, message: PlatformMessage, tools: TranscriptTools
) -> None:
    await adapter.on_event(
        AgentInput(
            msg=message,
            tools=tools,
            history=HistoryProvider(raw=[]),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id=message.room_id,
        )
    )


class InitializationObserver(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() == STDERR_LINE_LOG_TEMPLATE % INITIALIZE_PENDING_LINE:
            self.started.set()
