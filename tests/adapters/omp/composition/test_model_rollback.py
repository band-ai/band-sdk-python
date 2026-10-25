"""A cancelled native model rollback preserves the accepted selection."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.omp import OmpPermissionRequest
from band.core.model_catalog import ModelSelection
from band.integrations.omp import OMP_FORM_APPROVE
from band.runtime.tools import AgentTools
from tests.adapters.lifecycle.runtime import ReplyingPlatform
from tests.adapters.omp.kit import claimed_workspace_rooms, run_turn
from tests.adapters.omp.mailboxkit import (
    mailbox_adapter,
    peer_frames,
    received_ui_responses,
    room_client,
    selection_order,
)
from tests.adapters.omp.mailboxpeer import (
    MODEL_PROVIDER,
    ORIGINAL_MODEL,
    PUBLIC_MODEL,
    REJECTED_MODEL,
    SECOND_CALLBACK,
    UNSUPPORTED_EFFORT,
    PeerCommand,
    model_reply,
)
from tests.adapters.omp.retirementkit import native_overlay
from tests.integrations.omprpc.kit import owned_rpc_process
from tests.runtime.helpers import ALICE


class TestModelRollback:
    async def test_cancelling_callback_rollback_recovers_the_accepted_selection(
        self, tmp_path: Path, tools: AgentTools, platform: ReplyingPlatform
    ) -> None:
        async def change_model(request: OmpPermissionRequest) -> str:
            await adapter.apply_model_selection(
                ModelSelection(
                    model=f"{MODEL_PROVIDER}/{REJECTED_MODEL}",
                    reasoning_effort=UNSUPPORTED_EFFORT,
                ),
                room_id=request.room_id,
            )
            return OMP_FORM_APPROVE

        transcript = tmp_path / "rollback.jsonl"
        adapter = mailbox_adapter(
            tmp_path, change_model, callback_turn=2, transcript=transcript
        )
        turn: asyncio.Task[None] | None = None
        try:
            await run_turn(adapter, tools)
            await adapter.apply_model_selection(
                ModelSelection(model=f"{MODEL_PROVIDER}/{PUBLIC_MODEL}"),
                room_id=tools.room_id,
            )
            client = room_client(adapter)
            process = owned_rpc_process(client)
            overlay = native_overlay(client)
            await client.request(PeerCommand.HOLD_MODEL_RESPONSE, modelId=PUBLIC_MODEL)
            current_tools = AgentTools(tools.room_id, tools.rest, participants=[ALICE])
            turn = asyncio.create_task(
                run_turn(adapter, current_tools, bootstrap=False)
            )
            await client.request(PeerCommand.WAIT_MODEL_RESPONSE)
            assert selection_order(await peer_frames(adapter)) == [
                ORIGINAL_MODEL,
                PUBLIC_MODEL,
                REJECTED_MODEL,
                PUBLIC_MODEL,
            ]
            assert not turn.done()
            await client.request(PeerCommand.PERMISSION, callbackId=SECOND_CALLBACK)
            await peer_frames(adapter)
            turn.cancel()
            with pytest.raises(asyncio.CancelledError):
                await turn
            assert process.returncode is not None and not overlay.exists()
            assert claimed_workspace_rooms(adapter) == ()
            assert received_ui_responses(transcript) == []

            follow_up = AgentTools(tools.room_id, tools.rest, participants=[ALICE])
            await run_turn(adapter, follow_up)
            assert platform.replies == [
                model_reply(ORIGINAL_MODEL),
                model_reply(PUBLIC_MODEL),
            ]
        finally:
            if turn is not None:
                turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)
            await adapter.cleanup_all()
