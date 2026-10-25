"""Hold real permission callbacks through cancellation settlement."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
from pathlib import Path

from band.adapters.omp import OmpAdapter, OmpPermissionRequest
from band.integrations.omp import OMP_FORM_APPROVE
from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import OmpFrameType
from tests.adapters.hold import Hold
from tests.adapters.omp.kit import claimed_workspace_rooms, run_turn
from tests.adapters.omp.mailboxkit import (
    AdmissionMailbox,
    MailboxTools,
    mailbox_adapter,
    peer_frames,
    room_client,
)
from tests.adapters.omp.mailboxpeer import FIRST_CALLBACK, SECOND_CALLBACK, PeerCommand
from tests.integrations.omprpc.kit import owned_rpc_process


class SettlingPermission:
    """Keep callback cleanup visible after native cancellation is delivered."""

    def __init__(self, *, cleanup_inside: bool = False) -> None:
        self.adapter: OmpAdapter | None = None
        self.entered = asyncio.Event()
        self.settling = Hold()
        self.finished = asyncio.Event()
        self.calls: list[str] = []
        self.cleanup_inside = cleanup_inside
        self.trace: list[CallbackExit] = []

    async def __call__(self, request: OmpPermissionRequest) -> str:
        if self.finished.is_set():
            return OMP_FORM_APPROVE
        self.calls.append(request.title)
        self.entered.set()
        try:
            if self.cleanup_inside:
                assert self.adapter is not None
                await self.adapter.on_cleanup(request.room_id)
                await self.settling.wait()
            else:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    await self.settling.wait()
        finally:
            self.trace.append(CallbackExit.HANDLER)
            self.finished.set()
        return OMP_FORM_APPROVE


def native_overlay(client: OmpRpcClient) -> Path:
    """The startup overlay whose lifetime belongs to this native connection."""
    return Path(client.command[client.command.index("--plugin-dir") + 1])


async def exercise_callback_retirement(tmp_path: Path) -> None:
    resolver = SettlingPermission()
    adapter = mailbox_adapter(tmp_path, resolver)
    resolver.adapter = adapter
    mailbox = AdmissionMailbox()
    adapter._mailbox = mailbox
    old_tools = MailboxTools()
    turn = asyncio.create_task(run_turn(adapter, old_tools))
    await resolver.entered.wait()
    client = room_client(adapter)
    process = owned_rpc_process(client)
    overlay = native_overlay(client)
    mailbox.discard_admissions()
    await client.request(PeerCommand.PERMISSION, callbackId=SECOND_CALLBACK)
    await mailbox.admitted.get()
    cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))
    try:
        await resolver.settling.arrived()
        assert not cleanup.done()
        assert process.returncode is None and overlay.exists()
        assert claimed_workspace_rooms(adapter) == ("room-123",)
        assert resolver.calls == [FIRST_CALLBACK]
        resolver.settling.released.set()
        await cleanup
        await asyncio.gather(turn, return_exceptions=True)
        assert resolver.finished.is_set()
        assert process.returncode is not None and not overlay.exists()
        assert claimed_workspace_rooms(adapter) == ()
        assert old_tools.chat == []

        follow_up = MailboxTools()
        # Each replacement peer starts its callback sequence afresh.
        await run_turn(adapter, follow_up)
        assert room_client(adapter) is not client
        assert follow_up.chat and old_tools.chat == []
        frames = await peer_frames(adapter)
        assert not any(
            frame["type"] == OmpFrameType.UI_RESPONSE
            and frame.get("id") == SECOND_CALLBACK
            for frame in frames
        )
    finally:
        resolver.settling.released.set()
        await asyncio.gather(turn, cleanup, return_exceptions=True)
        await adapter.cleanup_all()


class CallbackExit(StrEnum):
    HANDLER = "handler"
    SCHEDULER = "scheduler"


class ObservedScheduler:
    """Observe the first RPC-owned task through the public scheduler hook."""

    def __init__(
        self,
        schedule: Callable[[Callable[[], Awaitable[None]]], Awaitable[None]],
        trace: list[CallbackExit],
    ) -> None:
        self.schedule = schedule
        self.trace = trace
        self.first: asyncio.Task[None] | None = None

    async def __call__(self, run: Callable[[], Awaitable[None]]) -> None:
        task = asyncio.current_task()
        if self.first is None:
            self.first = task
        try:
            await self.schedule(run)
        finally:
            if task is self.first:
                self.trace.append(CallbackExit.SCHEDULER)
