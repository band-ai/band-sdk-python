"""A crashed stdio ACP agent's exit and stderr reach the runtime log."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from band.core.types import PlatformMessage
from band.integrations.acp.client_adapter import ACPClientAdapter
from band.integrations.acp.client_runtime import STDERR_TAIL_LINES
from tests.integrations.acp.acp_toolkit.harness import TranscriptTools
from tests.integrations.acp.acp_toolkit.peer import (
    RECOVERED_REPLY,
    ExitStage,
    stdio_peer_config,
)
from tests.integrations.acp.clientruntime.kit import (
    RUNTIME_LOGGER,
    InitializationObserver,
    StdioRuntimeFactory,
    assert_crash_report,
    deliver_stdio_turn,
    runtime_warnings,
)
from tests.paths import REPO_ROOT


class TestStdioCrashReporting:
    """A crashed stdio ACP agent is reported with its exit code and stderr
    tail; a deliberate stop or a cancelled start is not."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stage",
        [
            ExitStage.INITIALIZE,
            ExitStage.PROMPT,
            ExitStage.STDOUT_EOF,
            ExitStage.PROTOCOL_ERROR,
        ],
    )
    async def test_crashed_stdio_agent_reports_exit_and_stderr(
        self,
        stdio_runtime: StdioRuntimeFactory,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
        stage: ExitStage,
    ) -> None:
        lines = ["authentication failed", "peer crashed"]
        with caplog.at_level(logging.WARNING, logger=RUNTIME_LOGGER):
            async with stdio_runtime(stage, lines) as runtime:
                with pytest.raises(ConnectionError, match="Connection closed"):
                    await runtime.start()
                    session_id = await runtime.create_session(
                        cwd=str(tmp_path), mcp_servers=[]
                    )
                    await runtime.prompt(session_id=session_id, prompt_text="crash")

        assert_crash_report(caplog, lines)

    @pytest.mark.asyncio
    async def test_crashed_stdio_agent_reports_only_the_stderr_tail(
        self, stdio_runtime: StdioRuntimeFactory, caplog: pytest.LogCaptureFixture
    ) -> None:
        lines = [f"diagnostic {index}" for index in range(STDERR_TAIL_LINES + 5)]
        with caplog.at_level(logging.WARNING, logger=RUNTIME_LOGGER):
            async with stdio_runtime(ExitStage.INITIALIZE, lines) as runtime:
                with pytest.raises(ConnectionError, match="Connection closed"):
                    await runtime.start()

        [warning] = runtime_warnings(caplog)
        assert warning.getMessage().splitlines()[1:] == lines[-STDERR_TAIL_LINES:]

    @pytest.mark.asyncio
    async def test_deliberate_stdio_stop_does_not_warn_about_nonzero_exit(
        self, stdio_runtime: StdioRuntimeFactory, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=RUNTIME_LOGGER):
            async with stdio_runtime(
                ExitStage.EOF, ["deliberately stopped"]
            ) as runtime:
                await runtime.start()

        assert runtime_warnings(caplog) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stage", [ExitStage.STDOUT_EOF, ExitStage.PROTOCOL_ERROR])
    async def test_failed_stdio_turn_reports_crash_and_next_turn_recovers(
        self,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
        sample_platform_message: PlatformMessage,
        stage: ExitStage,
    ) -> None:
        lines = ["fatal peer diagnostic"]
        adapter = ACPClientAdapter(
            stdio_peer_config(stage, lines, crash_marker=tmp_path / "crashed"),
            workspace_for_room=lambda room_id: str(REPO_ROOT),
        )
        failed = TranscriptTools()
        recovered = TranscriptTools()
        with caplog.at_level(logging.WARNING, logger=RUNTIME_LOGGER):
            await adapter.on_started("Stdio peer", "Real subprocess")
            try:
                with pytest.raises(ConnectionError, match="Connection closed"):
                    await deliver_stdio_turn(adapter, sample_platform_message, failed)
                await deliver_stdio_turn(adapter, sample_platform_message, recovered)
            finally:
                await adapter.stop()

        assert_crash_report(caplog, lines)
        assert failed.reply.texts == []
        assert len(failed.reply.errors) == 1
        assert recovered.reply.texts == [RECOVERED_REPLY]
        assert recovered.reply.errors == []
        assert recovered.turn.replied

    @pytest.mark.asyncio
    async def test_cancelled_stdio_start_does_not_warn_about_nonzero_exit(
        self,
        stdio_runtime: StdioRuntimeFactory,
        initialization_observer: InitializationObserver,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger=RUNTIME_LOGGER):
            async with stdio_runtime(
                ExitStage.INITIALIZE_WAIT, ["cancelled initialization"]
            ) as runtime:
                startup = asyncio.create_task(runtime.start())
                try:
                    await initialization_observer.started.wait()
                    startup.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await startup
                finally:
                    startup.cancel()
                    await asyncio.gather(startup, return_exceptions=True)

        assert runtime_warnings(caplog) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stage",
        [
            ExitStage.NEW_SESSION_PROTOCOL_ERROR,
            ExitStage.LOAD_SESSION_PROTOCOL_ERROR,
            ExitStage.CONFIG_PROTOCOL_ERROR,
        ],
    )
    async def test_session_operation_protocol_failure_retains_crash_diagnostics(
        self,
        stdio_runtime: StdioRuntimeFactory,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
        stage: ExitStage,
    ) -> None:
        lines = ["fatal session operation"]
        with caplog.at_level(logging.WARNING, logger=RUNTIME_LOGGER):
            async with stdio_runtime(stage, lines) as runtime:
                await runtime.start()
                with pytest.raises(ConnectionError, match="Connection closed"):
                    match stage:
                        case ExitStage.NEW_SESSION_PROTOCOL_ERROR:
                            await runtime.create_session(
                                cwd=str(tmp_path), mcp_servers=[]
                            )
                        case ExitStage.LOAD_SESSION_PROTOCOL_ERROR:
                            await runtime.load_session(
                                cwd=str(tmp_path),
                                session_id="persisted",
                                mcp_servers=[],
                            )
                        case ExitStage.CONFIG_PROTOCOL_ERROR:
                            session_id = await runtime.create_session(
                                cwd=str(tmp_path), mcp_servers=[]
                            )
                            await runtime.set_config_option(
                                session_id=session_id, config_id="model", value="model"
                            )
        assert_crash_report(caplog, lines)
