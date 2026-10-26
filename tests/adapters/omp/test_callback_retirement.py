"""Real scheduled callbacks retain native custody until cancellation settles."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from pathlib import Path

from band.integrations.omprpc.prompt import OmpCallbackKind
from tests.adapters.omp.kit import checkpoint, run_turn, start_cleanup
from tests.adapters.omp.mailboxkit import MailboxTools, mailbox_adapter, room_client
from tests.adapters.omp.mailboxpeer import (
    FIRST_CALLBACK,
    ORIGINAL_MODEL,
    PeerCommand,
    model_reply,
)
from tests.adapters.omp.retirementkit import (
    CallbackExit,
    ObservedScheduler,
    SettlingPermission,
    exercise_callback_retirement,
    native_overlay,
)
from tests.integrations.omprpc.kit import owned_rpc_process


class TestCallbackRetirement:
    async def test_callback_requested_cleanup_joins_its_own_settlement(
        self, tmp_path: Path
    ) -> None:
        resolver = SettlingPermission(cleanup_inside=True)
        adapter = mailbox_adapter(tmp_path, resolver)
        resolver.adapter = adapter
        tools = MailboxTools()
        turn = asyncio.create_task(run_turn(adapter, tools))
        cleanup: asyncio.Task[None] | None = None
        try:
            await resolver.settling.arrived()
            client = room_client(adapter)
            process = owned_rpc_process(client)
            overlay = native_overlay(client)
            cleanup = await start_cleanup(adapter)
            assert not turn.done() and not cleanup.done()
            assert overlay.exists() and process.returncode is None
            resolver.settling.released.set()
            await asyncio.gather(turn, cleanup)
            assert resolver.finished.is_set()
            assert tools.chat == [model_reply(ORIGINAL_MODEL)]
            assert not overlay.exists() and process.returncode is not None
            follow_up = MailboxTools()
            await run_turn(adapter, follow_up)
            assert follow_up.chat == [model_reply(ORIGINAL_MODEL)]
        finally:
            resolver.settling.released.set()
            turn.cancel()
            tasks = [turn, *([cleanup] if cleanup is not None else [])]
            await asyncio.gather(*tasks, return_exceptions=True)
            await adapter.cleanup_all()

    async def test_cleanup_keeps_custody_until_the_running_callback_settles(
        self, tmp_path: Path
    ) -> None:
        await exercise_callback_retirement(tmp_path)

    async def test_second_cancellation_keeps_custody_until_callback_settlement(
        self, tmp_path: Path
    ) -> None:
        resolver = SettlingPermission()
        adapter = mailbox_adapter(tmp_path, resolver, callback_turn=2)
        resolver.adapter = adapter
        turn: asyncio.Task[None] | None = None
        cleanup: asyncio.Task[None] | None = None
        try:
            await run_turn(adapter, MailboxTools())
            client = room_client(adapter)
            assert client.schedule_callback is not None
            scheduler = ObservedScheduler(client.schedule_callback, resolver.trace)
            client.schedule_callback = scheduler
            joining = asyncio.Event()

            async def join_close(closing: Awaitable[None]) -> None:
                joining.set()
                await asyncio.shield(closing)

            client.join_close = join_close
            process = owned_rpc_process(client)
            overlay = native_overlay(client)
            turn = asyncio.create_task(
                run_turn(adapter, MailboxTools(), bootstrap=False)
            )
            await resolver.entered.wait()
            await client.request(
                PeerCommand.CANCEL_CALLBACK,
                targetId=FIRST_CALLBACK,
                kind=OmpCallbackKind.UI,
            )
            await resolver.settling.arrived()
            cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))
            await joining.wait()
            await checkpoint()
            assert scheduler.first is not None and scheduler.first.cancelling() >= 2
            assert not resolver.finished.is_set()
            assert overlay.exists() and process.returncode is None
            resolver.settling.released.set()
            await cleanup
            await asyncio.gather(turn, return_exceptions=True)
            assert resolver.trace == [CallbackExit.HANDLER, CallbackExit.SCHEDULER]
            assert not overlay.exists() and process.returncode is not None
            follow_up = MailboxTools()
            await run_turn(adapter, follow_up)
            assert follow_up.chat == [model_reply(ORIGINAL_MODEL)]
        finally:
            resolver.settling.released.set()
            tasks = [task for task in (turn, cleanup) if task is not None]
            if turn is not None:
                turn.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await adapter.cleanup_all()
