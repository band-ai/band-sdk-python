"""An exited leader cannot leave close waiting forever on inherited streams."""

from __future__ import annotations

import asyncio
import os
import signal
import sys

import pytest

from band.contracts.models import ProcessOptions, ProcessResult
from band.contracts.process import ManagedProcess
from tests.contracts.lifecycle.descendants import (
    DescendantFence,
    DescendantPeer,
    ExitedSpawn,
)
from tests.paths import REPO_ROOT


class TestDescendants:
    @pytest.mark.looptime
    async def test_cleanup_deadline_covers_streams_after_the_leader_exits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spawn = ExitedSpawn()
        spawn.child.ready()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(os, "killpg", spawn.kill_group, raising=False)
        owner = ManagedProcess(
            ("owned-fixture",),
            options=ProcessOptions(shutdown_timeout=1.0),
            ready_event="ready",
        )
        await owner.start()
        closing = asyncio.create_task(owner.aclose())
        try:
            done, _ = await asyncio.wait({closing}, timeout=2)
            assert closing in done, "Cleanup has no deadline once the leader exits"
            result = await closing
            assert result is not None
            assert result.returncode == 0
            assert result.forced
        finally:
            spawn.exited_child.close_pipes()
            await asyncio.gather(closing, return_exceptions=True)

    @pytest.mark.contract_linux
    async def test_real_descendant_exits_after_its_leader_exits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        peer = DescendantPeer()
        port = await peer.start()
        monkeypatch.setattr(
            asyncio.subprocess, "SubprocessStreamProtocol", peer.protocol
        )
        owner = ManagedProcess(
            (
                sys.executable,
                "-I",
                "-u",
                str(REPO_ROOT / "tests/contracts/lifecycle/descendant.py"),
                "leader",
                str(port),
            ),
            options=ProcessOptions(shutdown_timeout=0.05),
            ready_event="ready",
        )
        fence: DescendantFence | None = None
        closing: asyncio.Task[ProcessResult | None] | None = None
        try:
            event = await owner.start()
            leader = await peer.ready("leader")
            descendant = await peer.ready("descendant")
            assert owner.pid == leader.pid
            assert event["descendant"] == descendant.pid
            fence = DescendantFence(descendant.pid, leader.pid)
            leader.writer.write(b"exit")
            await leader.writer.drain()
            await asyncio.wait_for(peer.leader_exited.wait(), 10)
            assert not owner.running
            assert not fence.exited.done()
            closing = asyncio.create_task(owner.aclose())
            done, _ = await asyncio.wait({closing}, timeout=10)
            assert closing in done, "An owned descendant kept cleanup blocked"
            result = await closing
            assert result is not None
            assert result.returncode == 0
            assert result.forced
            await asyncio.wait_for(asyncio.shield(fence.exited), 10)
        finally:
            try:
                if fence is not None:
                    await fence.close()
                elif owner.pid is not None:
                    try:
                        os.killpg(owner.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            finally:
                try:
                    if closing is None:
                        closing = asyncio.create_task(owner.aclose())
                    done, _ = await asyncio.wait({closing}, timeout=10)
                    assert closing in done, "Owned fixture cleanup did not complete"
                    await asyncio.gather(closing, return_exceptions=True)
                finally:
                    await peer.close()
