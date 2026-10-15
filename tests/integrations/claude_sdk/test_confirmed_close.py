"""The native transport cannot forget a child before its exit is confirmed."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions

from band.integrations.claude_sdk import transport
from tests.adapters.claude_sdk.process import ForgetfulProcess, WorkspaceProcess


@asynccontextmanager
async def started(
    child_type: type[WorkspaceProcess], tmp_path: Path
) -> AsyncIterator[tuple[transport.ConfirmedCloseTransport, WorkspaceProcess]]:
    native = child_type(
        prompt="",
        options=ClaudeAgentOptions(
            cli_path=sys.executable, cwd=str(tmp_path), env={"PROBE_MARKER": "close"}
        ),
    )
    owned = transport.ConfirmedCloseTransport(native)
    await owned.connect()
    child = native._child
    kill, wait = type(child).kill, type(child).wait
    try:
        yield owned, native
    finally:
        # Reap with unpatched operations even when the assertion under test fails.
        if child.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                kill(child)
            async with asyncio.timeout(10):
                await wait(child)


class TestConfirmedClose:
    async def test_a_close_that_falsely_succeeds_still_reaps_the_child(
        self, tmp_path: Path
    ) -> None:
        async with started(ForgetfulProcess, tmp_path) as (owned, native):
            await owned.close()
            assert native.exited
            assert not owned.close_pending

    async def test_an_unconfirmed_exit_keeps_the_child_for_a_retry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with started(ForgetfulProcess, tmp_path) as (owned, native):
            child = native._child
            real_kill = child.kill
            monkeypatch.setattr(
                child, "kill", lambda: (_ for _ in ()).throw(PermissionError())
            )
            with pytest.raises(transport.ClaudeProcessCloseError):
                await owned.close()
            assert owned.close_pending
            assert not native.exited

            monkeypatch.setattr(child, "kill", real_kill)
            await owned.close()
            assert native.exited
            assert not owned.close_pending

    async def test_an_exit_that_never_confirms_fails_within_the_bound(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(transport, "CHILD_EXIT_TIMEOUT_SECONDS", 0.2)
        async with started(ForgetfulProcess, tmp_path) as (owned, native):
            child = native._child
            real_wait = child.wait

            async def never() -> int:
                await asyncio.Event().wait()
                return 0

            monkeypatch.setattr(child, "wait", never)
            async with asyncio.timeout(5):
                with pytest.raises(transport.ClaudeProcessCloseError):
                    await owned.close()
            assert owned.close_pending

            monkeypatch.setattr(child, "wait", real_wait)
            await owned.close()
            assert native.exited
            assert not owned.close_pending

    async def test_a_close_that_raised_keeps_the_child_for_a_retry(
        self, tmp_path: Path
    ) -> None:
        async with started(WorkspaceProcess, tmp_path) as (owned, native):
            native.refuse_close = True
            with pytest.raises(RuntimeError, match="cleanup failed"):
                await owned.close()
            assert owned.close_pending
            assert not native.exited

            native.refuse_close = False
            await owned.close()
            assert native.exited
            assert not owned.close_pending
