"""Host cleanup must preserve the room owner and still drain failed probes."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.integrations.claude_sdk.session_manager import (
    ClaudeSessionManagerStoppedError,
)
from tests.adapters.claude_sdk.helpers import OpenRoom
from tests.adapters.claude_sdk.hostprobe import HostProbeProcess, host_probe


class TestProbeCleanupComposition:
    async def test_a_failed_room_shutdown_still_reaps_a_retained_probe(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def refuse(child: HostProbeProcess) -> None:
            child.refuse_close = True

        probe = host_probe(monkeypatch, tmp_path, on_launch=refuse)
        await probe.adapter.on_started("Host", "Probe cleanup composition")
        backend = probe.adapter._mcp.current
        assert backend is not None
        stop = backend.stop

        async def failed_stop() -> None:
            raise RuntimeError("Band server stop failed")

        try:
            with pytest.raises(CleanupIncompleteError):
                await probe.adapter.preflight(timeout_s=5)
            [child] = probe.children
            child.refuse_close = False
            object.__setattr__(backend, "stop", failed_stop)
            with pytest.raises(RuntimeError, match="Band server stop failed"):
                await probe.adapter.cleanup_all()
            assert child.exited
        finally:
            object.__setattr__(backend, "stop", stop)
            for child in probe.children:
                child.refuse_close = False
            await probe.adapter.cleanup_all()

    async def test_a_cancelled_room_shutdown_still_reaps_a_retained_probe(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def refuse(child: HostProbeProcess) -> None:
            child.refuse_close = True

        probe = host_probe(monkeypatch, tmp_path, on_launch=refuse)
        await probe.adapter.on_started("Host", "Probe cleanup composition")
        backend = probe.adapter._mcp.current
        assert backend is not None
        stop = backend.stop
        entered, release = asyncio.Event(), asyncio.Event()
        closing: asyncio.Task[None] | None = None

        async def held_stop() -> None:
            entered.set()
            await release.wait()
            await stop()

        try:
            with pytest.raises(CleanupIncompleteError):
                await probe.adapter.preflight(timeout_s=5)
            [child] = probe.children
            child.refuse_close = False
            object.__setattr__(backend, "stop", held_stop)
            closing = asyncio.create_task(probe.adapter.cleanup_all())
            await entered.wait()
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert child.exited
        finally:
            release.set()
            object.__setattr__(backend, "stop", stop)
            for child in probe.children:
                child.refuse_close = False
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)
            await probe.adapter.cleanup_all()

    async def test_cleanup_from_accepted_work_preserves_the_requester(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        room.claude.script([room.model_reply("ready")])
        await room.send("start")
        manager = room.adapter._session_manager
        assert manager is not None
        session = manager.session(room.room_id)
        assert session is not None
        owner = room.adapter._harness_probe
        close_allowed = False
        closed = asyncio.Event()

        async def close_probe() -> None:
            if not close_allowed:
                raise RuntimeError("probe close held")
            closed.set()

        async def discover() -> None:
            owner.own(close_probe)

        with pytest.raises(CleanupIncompleteError):
            await owner.run(discover, timeout_s=5)
        close_allowed = True
        async with session.retained.use():
            async with asyncio.timeout(2):
                with suppress(CleanupIncompleteError):
                    await room.adapter.cleanup_all()
            assert closed.is_set()
            with pytest.raises(ClaudeSessionManagerStoppedError):
                manager.room("after-self-cleanup")
        await room.adapter.cleanup_all()
