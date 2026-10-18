"""A failed native close must not consume the next room input or selection."""

from __future__ import annotations

import sys
from functools import partial
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from band.adapters.omp import OmpAdapter
from band.converters.omp import OmpSessionState
from band.core.model_catalog import ModelSelection
from band.integrations.omprpc import OmpRpcClient
from band.runtime.tools import AgentTools
from band.testing import FakeAgentTools, failure_reports
from tests.adapters.test_omp_catalog import catalog_adapter
from tests.integrations.acp.conftest import make_platform_message


class RoomParameters(BaseModel):
    type: str
    provider: str | None = None
    modelId: str | None = None


class RoomReceipt(BaseModel):
    event: Literal["spawn", "rpc"]
    pid: int
    parameters: RoomParameters | None = None


@pytest.mark.asyncio
class TestOmpRoomRecovery:
    async def _turn(self, adapter: OmpAdapter, *, bootstrap: bool) -> FakeAgentTools:
        tools = FakeAgentTools(room_id="room-123")
        tools.execute_tool_call_structured = partial(
            AgentTools.execute_tool_call_structured, tools
        )
        await adapter.run_judged_turn(
            msg=make_platform_message("Reply to the current input"),
            tools=tools,
            history=OmpSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=bootstrap,
            room_id="room-123",
        )
        return tools

    @pytest.mark.parametrize(
        ("select_model", "wrapped_launcher"),
        [(False, False), (True, False), (True, True)],
    )
    async def test_next_input_retires_failed_close_before_reopening(
        self, tmp_path: Path, monkeypatch, select_model: bool, wrapped_launcher: bool
    ) -> None:
        monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", AsyncMock())
        adapter, receipts = catalog_adapter(tmp_path, "room-recovery")
        if wrapped_launcher:
            adapter.config = adapter.config.model_copy(
                update={
                    "command": (
                        sys.executable,
                        "-u",
                        "-c",
                        (
                            "import subprocess, sys; "
                            "raise SystemExit(subprocess.call(sys.argv[1:]))"
                        ),
                        *adapter.config.command,
                    )
                }
            )
        try:
            first = await self._turn(adapter, bootstrap=True)
            assert first.chat == ["Room reply delivered"]
            old = adapter._rooms["room-123"]
            process = old.runtime._proc
            assert process is not None and process.returncode is None
            original_close = OmpRpcClient._close_process
            failed = False

            async def close(runtime: OmpRpcClient) -> None:
                nonlocal failed
                if runtime is old.runtime and not failed:
                    failed = True
                    assert runtime._closing
                    raise OSError("Transient native close failure")
                await original_close(runtime)

            monkeypatch.setattr(OmpRpcClient, "_close_process", close)
            with pytest.raises(OSError):
                await adapter.on_cleanup("room-123")
            assert old.runtime._closing
            assert process.returncode is None
            if select_model:
                await adapter.apply_model_selection(
                    ModelSelection(model="local/plain"), room_id="room-123"
                )
            second = await self._turn(adapter, bootstrap=False)
            assert second.chat == ["Room reply delivered"]
            assert not failure_reports(second)
            assert process.returncode is not None
            replacement = adapter._rooms["room-123"]
            assert replacement is not old
            if select_model:
                observed = [
                    RoomReceipt.model_validate_json(line)
                    for line in receipts.read_text(encoding="utf-8").splitlines()
                ]
                # The owned launcher can have a different PID from its native peer.
                replacement_pid = next(
                    entry.pid for entry in reversed(observed) if entry.event == "spawn"
                )
                assert any(
                    entry.pid == replacement_pid
                    and entry.parameters is not None
                    and entry.parameters.type == "set_model"
                    and entry.parameters.provider == "local"
                    and entry.parameters.modelId == "plain"
                    for entry in observed
                )
        finally:
            await adapter.cleanup_all()
