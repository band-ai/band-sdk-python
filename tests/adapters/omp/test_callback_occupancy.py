"""Native callback occupancy preserves FIFO mutations and independent rooms."""

from __future__ import annotations

import asyncio
from pathlib import Path

from band.core.model_catalog import ModelSelection
from band.testing import failure_reports
from tests.adapters.omp.childselection import ChildSelectingResolver
from tests.adapters.omp.kit import run_turn
from tests.adapters.omp.mailboxkit import (
    AdmissionMailbox,
    CallbackResolver,
    MailboxTools,
    mailbox_adapter,
    peer_frames,
    room_client,
    selection_order,
)
from tests.adapters.omp.mailboxpeer import (
    CALLBACK_MODEL,
    FIRST_CALLBACK,
    MODEL_PROVIDER,
    ORIGINAL_MODEL,
    PUBLIC_MODEL,
    SECOND_CALLBACK,
    SECOND_MODEL,
    PeerCommand,
    model_reply,
)


class TestCallbackOccupancy:
    async def test_spawned_selection_cannot_bypass_the_current_job(
        self, tmp_path: Path
    ) -> None:
        resolver = ChildSelectingResolver()
        adapter = mailbox_adapter(tmp_path, resolver)
        resolver.adapter = adapter
        try:
            tools = MailboxTools()
            await run_turn(adapter, tools)
            assert resolver.child_rejected
            assert selection_order(await peer_frames(adapter)) == [
                ORIGINAL_MODEL,
                CALLBACK_MODEL,
                FIRST_CALLBACK,
            ]
            assert tools.chat == [model_reply(CALLBACK_MODEL)]
            assert failure_reports(tools) == []
        finally:
            await adapter.cleanup_all()

    async def test_callback_and_public_selection_follow_admitted_fifo(
        self, tmp_path: Path
    ) -> None:
        resolver = CallbackResolver(held=True)
        adapter = mailbox_adapter(tmp_path, resolver)
        resolver.adapter = adapter
        mailbox = AdmissionMailbox()
        adapter._mailbox = mailbox
        tools = MailboxTools()
        turn = asyncio.create_task(run_turn(adapter, tools))
        selection = None
        try:
            await resolver.first.arrived()
            mailbox.discard_admissions()
            await room_client(adapter).request(
                PeerCommand.PERMISSION, callbackId=SECOND_CALLBACK
            )
            assert await mailbox.admitted.get() == "room-123"
            selection = asyncio.create_task(
                adapter.apply_model_selection(
                    ModelSelection(model=f"{MODEL_PROVIDER}/{PUBLIC_MODEL}"),
                    room_id="room-123",
                )
            )
            assert await mailbox.admitted.get() == "room-123"
            assert resolver.calls == [FIRST_CALLBACK]
            assert selection_order(await peer_frames(adapter)) == [ORIGINAL_MODEL]
            assert not selection.done()

            resolver.first.released.set()
            await asyncio.gather(turn, selection)
            assert resolver.calls == [FIRST_CALLBACK, SECOND_CALLBACK]
            assert selection_order(await peer_frames(adapter)) == [
                ORIGINAL_MODEL,
                CALLBACK_MODEL,
                FIRST_CALLBACK,
                SECOND_MODEL,
                SECOND_CALLBACK,
                PUBLIC_MODEL,
            ]
            assert failure_reports(tools) == []
            follow_up = MailboxTools()
            await run_turn(adapter, follow_up, bootstrap=False)
            assert follow_up.chat == [model_reply(PUBLIC_MODEL)]
        finally:
            resolver.first.released.set()
            tasks = [turn, *([selection] if selection is not None else [])]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await adapter.cleanup_all()

    async def test_blocked_room_callback_does_not_block_another_room(
        self, tmp_path: Path
    ) -> None:
        resolver = CallbackResolver(held=True)
        adapter = mailbox_adapter(tmp_path, resolver, callback_turn=2)
        resolver.adapter = adapter
        first_tools = MailboxTools()
        await run_turn(adapter, first_tools)
        blocked_tools = MailboxTools()
        turn = asyncio.create_task(run_turn(adapter, blocked_tools, bootstrap=False))
        try:
            await resolver.first.arrived()
            other_tools = MailboxTools()
            await run_turn(adapter, other_tools, room_id="other-room")
            assert other_tools.chat == [model_reply(ORIGINAL_MODEL)]
            assert failure_reports(other_tools) == []
            assert selection_order(await peer_frames(adapter, "other-room")) == [
                ORIGINAL_MODEL
            ]
            assert selection_order(await peer_frames(adapter)) == [ORIGINAL_MODEL]
            assert blocked_tools.chat == []
            assert not turn.done()
            resolver.first.released.set()
            await turn
            assert blocked_tools.chat == [model_reply(CALLBACK_MODEL)]
            assert other_tools.chat == [model_reply(ORIGINAL_MODEL)]
            follow_up = MailboxTools()
            await run_turn(adapter, follow_up, bootstrap=False)
            assert follow_up.chat == [model_reply(CALLBACK_MODEL)]
        finally:
            resolver.first.released.set()
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
            await adapter.cleanup_all()
