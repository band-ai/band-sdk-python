"""A room keeps its agent process until that process is confirmed closed."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from pathlib import Path

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.core.retention import Residency, RoomRetentionConfig
from band.integrations.acp import client_adapter, client_runtime
from band.integrations.acp.client_adapter import ACPClientAdapter, ACPTurnTimeoutError
from tests.integrations.acp.acp_toolkit import DEFAULT_ROOM
from tests.integrations.acp.acp_toolkit.harness import send_to
from tests.integrations.acp.acp_toolkit.peer import (
    EXIT_FAILURE,
    ExitStage,
    FailingExits,
    stdio_peer_config,
)
from tests.integrations.acp.roomownership.kit import runtime_warnings, terminate_pid
from tests.paths import REPO_ROOT


class TestProcessClosure:
    """A room keeps its agent process until that process is confirmed closed."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("stage", "turn_error"),
        [(ExitStage.EOF, None), (ExitStage.PROMPT, ACPTurnTimeoutError)],
        ids=["exits-at-close", "exited-before-close"],
    )
    async def test_a_descendant_holding_the_agents_pipes_keeps_its_room_owned(
        self,
        tmp_path: Path,
        stage: ExitStage,
        turn_error: type[Exception] | None,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The agent exits, at close or before it, but a background descendant
        still holds its stdout, so closure is unconfirmed: the room keeps its
        process until the descendant is gone and a retry confirms the exit."""
        monkeypatch.setattr(client_runtime, "STDOUT_HELD_WARNING_SECONDS", 0.1)
        holder_pid_file = tmp_path / "holder.pid"
        adapter = ACPClientAdapter(
            stdio_peer_config(
                stage,
                [],
                orphan_pid_file=holder_pid_file,
                retention=RoomRetentionConfig(transition_timeout_s=2.0),
                # A held stdout hides the agent's death, so its prompt only ends
                # at the turn deadline, whose recovery then waits on the pipes.
                turn_timeout_s=1.0,
            ),
            workspace_for_room=lambda room_id: str(REPO_ROOT),
        )
        await adapter.on_started("Stdio peer", "Real subprocess")
        try:
            with nullcontext() if turn_error is None else pytest.raises(turn_error):
                await send_to(adapter, DEFAULT_ROOM)
            room = adapter._rooms[DEFAULT_ROOM]
            runtime = room.runtime
            assert runtime is not None

            with pytest.raises(CleanupIncompleteError):
                await adapter.on_cleanup(DEFAULT_ROOM)

            assert room.runtime is runtime and not runtime.closed
            assert room.retained.snapshot.state == Residency.SUSPENDING
            process = runtime._transport
            assert isinstance(process, asyncio.subprocess.Process)
            held = f"ACP agent subprocess {process.pid} (exit code"
            assert any(warning.startswith(held) for warning in runtime_warnings(caplog))

            terminate_pid(int(holder_pid_file.read_text()))
            await adapter.on_cleanup(DEFAULT_ROOM)

            assert runtime.closed
            assert DEFAULT_ROOM not in adapter._rooms
        finally:
            if holder_pid_file.exists():
                terminate_pid(int(holder_pid_file.read_text()))
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_a_retry_after_a_failed_exit_kills_the_running_agent(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The agent's exit fails and leaves it running; a retry kills that same
        process and confirms its closure before the room is released."""
        adapter = ACPClientAdapter(
            stdio_peer_config(ExitStage.EOF, []),
            workspace_for_room=lambda room_id: str(REPO_ROOT),
        )
        async with FailingExits() as spawns:
            monkeypatch.setattr(client_adapter, "spawn_agent_process", spawns)
            await adapter.on_started("Stdio peer", "Real subprocess")
            try:
                await send_to(adapter, DEFAULT_ROOM)
                room = adapter._rooms[DEFAULT_ROOM]
                runtime = room.runtime
                [process] = spawns.processes

                with pytest.raises(RuntimeError, match=EXIT_FAILURE):
                    await adapter.on_cleanup(DEFAULT_ROOM)

                assert process.returncode is None
                assert room.runtime is runtime

                await adapter.on_cleanup(DEFAULT_ROOM)

                assert process.returncode is not None
                assert DEFAULT_ROOM not in adapter._rooms
                assert runtime_warnings(caplog) == [
                    f"ACP agent subprocess {process.pid} failed to exit",
                    f"Killing ACP agent subprocess {process.pid} after its exit failed",
                ]
            finally:
                await adapter.cleanup_all()
