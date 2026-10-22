"""Startup cancellation and concurrent close preserve ownership."""

from __future__ import annotations

import asyncio

import pytest

from band.contracts.errors import ContractProcessError
from tests.contracts.lifecycle.kit import Spawn


class TestOwnership:
    async def test_cancelled_spawn_is_joined_and_closed_before_cancellation_returns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spawn = Spawn()
        spawn.release.clear()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        owner = spawn.owner()
        started = asyncio.create_task(owner.start())
        await spawn.entered.wait()
        started.cancel()
        assert not started.done()
        spawn.release.set()
        with pytest.raises(asyncio.CancelledError):
            await started
        assert spawn.child.returncode == 0
        assert spawn.child.terminate_count == 1
        assert not owner.running

    async def test_concurrent_close_waits_for_confirmed_exit_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spawn = Spawn()
        spawn.child.ready()
        spawn.child.auto_exit = False
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        owner = spawn.owner()
        await owner.start()
        first = asyncio.create_task(owner.aclose())
        await spawn.child.terminated.wait()
        second = asyncio.create_task(owner.aclose())
        assert owner.running
        assert not first.done()
        spawn.child.finish()
        await asyncio.gather(first, second)
        assert spawn.child.terminate_count == 1
        assert not owner.running

    @pytest.mark.looptime
    async def test_startup_deadline_cleans_up_child_without_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spawn = Spawn()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        owner = spawn.owner()
        with pytest.raises(ContractProcessError, match="ready"):
            await owner.start()
        assert spawn.child.returncode == 0
        assert spawn.child.terminate_count == 1

    async def test_failure_exit_is_retained_for_the_operator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spawn = Spawn()
        spawn.child.finish(2)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        owner = spawn.owner()
        with pytest.raises(ContractProcessError):
            await owner.start()
        assert owner.result is not None
        assert owner.result.returncode == 2

    async def test_cancelling_close_waits_for_the_existing_child_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spawn = Spawn()
        spawn.child.ready()
        spawn.child.auto_exit = False
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        owner = spawn.owner()
        await owner.start()
        closing = asyncio.create_task(owner.aclose())
        await spawn.child.terminated.wait()
        closing.cancel()
        assert owner.running
        spawn.child.finish()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert not owner.running
        assert owner.result is not None
