"""An SDK close cannot release a child whose exit remains unconfirmed."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from band.adapters.claude_sdk import (
    ClaudeCLIOptions,
    ClaudeSDKAdapter,
    ClaudeSDKAdapterConfig,
)
from band.core.exceptions import CleanupIncompleteError
from tests.adapters.claude_sdk.process import WorkspacePeer, launch_children


async def test_sdk_close_retains_a_child_after_its_final_wait_times_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    adapter = ClaudeSDKAdapter(
        ClaudeSDKAdapterConfig(
            cwd=tmp_path, cli=ClaudeCLIOptions(cli_path=sys.executable)
        )
    )
    children: list[WorkspacePeer] = []
    try:
        with monkeypatch.context() as faults:

            class UnconfirmedPeer(WorkspacePeer):
                arguments = ("--stay",)

                async def connect(self) -> None:
                    await super().connect()

                    async def unconfirmed_wait() -> int:
                        raise TimeoutError

                    faults.setattr(self._child, "wait", unconfirmed_wait)
                    faults.setattr(self._child, "terminate", lambda: None)
                    faults.setattr(self._child, "kill", lambda: None)

            children = launch_children(faults, UnconfirmedPeer)
            with pytest.raises(CleanupIncompleteError):
                await adapter.preflight(timeout_s=5)
            [child] = children
            assert child._child.returncode is None
            with pytest.raises(CleanupIncompleteError):
                await adapter.cleanup_all()
            assert child._child.returncode is None

        await adapter.cleanup_all()
        assert child.exited
    finally:
        for child in children:
            if child._child.returncode is None:
                child._child.kill()
                await asyncio.wait_for(child._child.wait(), timeout=5)
        await adapter.cleanup_all()
