"""Native failure recovery crosses RPC, mailbox, turn tools, and room custody."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.core.model_catalog import ModelSelection
from band.integrations.ompwire import OmpFrameType
from band.runtime.tools import BandTool
from tests.adapters.omp.compoundkit import RollbackPermission, SettlingTools
from tests.adapters.omp.kit import claimed_workspace_rooms, run_turn
from tests.adapters.omp.mailboxkit import (
    AdmissionMailbox,
    CallbackResolver,
    MailboxTools,
    mailbox_adapter,
    peer_frames,
    received_callback_responses,
    received_ui_responses,
    room_client,
    selection_order,
)
from tests.adapters.omp.mailboxpeer import (
    FIRST_CALLBACK,
    MODEL_PROVIDER,
    ORIGINAL_MODEL,
    PUBLIC_MODEL,
    REJECTED_MODEL,
    SECOND_CALLBACK,
    PeerCommand,
    model_reply,
)
from tests.adapters.omp.retirementkit import native_overlay
from tests.integrations.omprpc.kit import owned_rpc_process


class TestCompoundCancellation:
    async def test_cancel_during_callback_rollback_recovers_the_accepted_selection(
        self, tmp_path: Path
    ) -> None:
        resolver = RollbackPermission()
        transcript = tmp_path / "rollback.jsonl"
        adapter = mailbox_adapter(
            tmp_path, resolver, callback_turn=2, transcript=transcript
        )
        resolver.adapter = adapter
        mailbox = AdmissionMailbox()
        adapter._mailbox = mailbox
        turn: asyncio.Task[None] | None = None
        try:
            await run_turn(adapter, MailboxTools())
            await adapter.apply_model_selection(
                ModelSelection(model=f"{MODEL_PROVIDER}/{PUBLIC_MODEL}"),
                room_id="room-123",
            )
            client = room_client(adapter)
            process = owned_rpc_process(client)
            overlay = native_overlay(client)
            old_tools = MailboxTools()
            turn = asyncio.create_task(run_turn(adapter, old_tools, bootstrap=False))
            await client.request(PeerCommand.WAIT_ROLLBACK)
            assert selection_order(await peer_frames(adapter)) == [
                ORIGINAL_MODEL,
                PUBLIC_MODEL,
                REJECTED_MODEL,
                PUBLIC_MODEL,
            ]
            assert not resolver.finished.is_set() and not turn.done()
            assert process.returncode is None and overlay.exists()
            mailbox.discard_admissions()
            await client.request(PeerCommand.PERMISSION, callbackId=SECOND_CALLBACK)
            await mailbox.admitted.get()
            turn.cancel()
            with pytest.raises(asyncio.CancelledError):
                await turn
            assert resolver.finished.is_set()
            assert resolver.calls == [FIRST_CALLBACK]
            assert old_tools.chat == []
            assert process.returncode is not None and not overlay.exists()
            assert claimed_workspace_rooms(adapter) == ()
            assert received_ui_responses(transcript) == []
            follow_up = MailboxTools()
            await run_turn(adapter, follow_up)
            assert room_client(adapter) is not client
            assert follow_up.chat == [model_reply(PUBLIC_MODEL)]
            recovered_callback = MailboxTools()
            await run_turn(adapter, recovered_callback, bootstrap=False)
            assert recovered_callback.chat == [model_reply(PUBLIC_MODEL)]
            assert resolver.calls == [FIRST_CALLBACK, FIRST_CALLBACK]
            assert old_tools.chat == []
        finally:
            if turn is not None:
                turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)
            await adapter.cleanup_all()

    async def test_cleanup_waits_for_running_band_tool_and_discards_queued_callback(
        self, tmp_path: Path
    ) -> None:
        resolver = CallbackResolver()
        transcript = tmp_path / "host-tool.jsonl"
        adapter = mailbox_adapter(
            tmp_path, resolver, transcript=transcript, initial_tool=True
        )
        resolver.adapter = adapter
        mailbox = AdmissionMailbox()
        adapter._mailbox = mailbox
        tools = SettlingTools()
        turn = asyncio.create_task(run_turn(adapter, tools))
        cleanup: asyncio.Task[None] | None = None
        try:
            await tools.entered.wait()
            client = room_client(adapter)
            process = owned_rpc_process(client)
            overlay = native_overlay(client)
            mailbox.discard_admissions()
            await client.request(PeerCommand.PERMISSION, callbackId=SECOND_CALLBACK)
            await mailbox.admitted.get()
            cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))
            await tools.settling.arrived()
            assert not cleanup.done() and not tools.finished.is_set()
            assert process.returncode is None and overlay.exists()
            assert claimed_workspace_rooms(adapter) == ("room-123",)
            assert resolver.calls == [] and tools.calls == [BandTool.GET_PARTICIPANTS]
            tools.settling.released.set()
            await cleanup
            await asyncio.gather(turn, return_exceptions=True)
            assert tools.finished.is_set()
            assert process.returncode is not None and not overlay.exists()
            assert claimed_workspace_rooms(adapter) == ()
            assert resolver.calls == [] and tools.chat == []
            assert received_ui_responses(transcript) == []
            assert (
                received_callback_responses(transcript, OmpFrameType.HOST_TOOL_RESULT)
                == []
            )
            follow_up = MailboxTools()
            await run_turn(adapter, follow_up)
            assert room_client(adapter) is not client
            assert follow_up.chat == [model_reply(ORIGINAL_MODEL)]
            assert tools.chat == []
        finally:
            tools.settling.released.set()
            turn.cancel()
            tasks = [turn, *([cleanup] if cleanup is not None else [])]
            await asyncio.gather(*tasks, return_exceptions=True)
            await adapter.cleanup_all()
