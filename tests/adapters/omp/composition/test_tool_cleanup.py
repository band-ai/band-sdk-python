"""Cleanup cancels a real Band HTTP tool before replacing its native process."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.integrations.ompwire import OmpFrameType
from band.runtime.tools import AgentTools
from tests.adapters.hold import Hold
from tests.adapters.lifecycle.runtime import ReplyingPlatform
from tests.adapters.omp.custody import claimed_workspace_rooms
from tests.adapters.omp.mailboxkit import (
    mailbox_adapter,
    peer_frames,
    received_callback_responses,
    received_ui_responses,
    room_client,
)
from tests.adapters.omp.mailboxpeer import (
    ORIGINAL_MODEL,
    SECOND_CALLBACK,
    PeerCommand,
    model_reply,
)
from tests.adapters.omp.retirementkit import native_overlay
from tests.adapters.omp.turns import run_turn
from tests.integrations.omprpc.processes import owned_rpc_process
from tests.runtime.helpers import ALICE


class TestToolCleanup:
    async def test_cleanup_cancels_an_http_tool_and_discards_the_queued_permission(
        self,
        tmp_path: Path,
        tools: AgentTools,
        platform: ReplyingPlatform,
        participants_request: Hold,
    ) -> None:
        transcript = tmp_path / "host-tool.jsonl"
        adapter = mailbox_adapter(tmp_path, transcript=transcript, initial_tool=True)
        turn = asyncio.create_task(run_turn(adapter, tools))
        try:
            await participants_request.arrived()
            client = room_client(adapter)
            process = owned_rpc_process(client)
            overlay = native_overlay(client)
            await client.request(PeerCommand.PERMISSION, callbackId=SECOND_CALLBACK)
            await peer_frames(adapter)
            assert process.returncode is None and overlay.exists()
            assert platform.replies == []

            await adapter.on_cleanup(tools.room_id)
            with pytest.raises(asyncio.CancelledError):
                await turn
            assert not participants_request.released.is_set()
            assert process.returncode is not None and not overlay.exists()
            assert claimed_workspace_rooms(adapter) == ()
            assert received_ui_responses(transcript) == []
            assert (
                received_callback_responses(transcript, OmpFrameType.HOST_TOOL_RESULT)
                == []
            )
            assert platform.replies == []

            participants_request.reached.clear()
            follow_up = AgentTools(tools.room_id, tools.rest, participants=[ALICE])
            turn = asyncio.create_task(run_turn(adapter, follow_up))
            await participants_request.arrived()
            await room_client(adapter).request(
                PeerCommand.PERMISSION, callbackId=SECOND_CALLBACK
            )
            await peer_frames(adapter)
            participants_request.released.set()
            await turn
            assert platform.replies == [model_reply(ORIGINAL_MODEL)]
            assert received_ui_responses(transcript) == [SECOND_CALLBACK]
        finally:
            participants_request.released.set()
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
            await adapter.cleanup_all()
