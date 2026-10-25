"""Interrupted selections cannot lend an uncertain provider to a pending turn."""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

import pytest

from band.core.model_catalog import ModelSelection
from band.integrations.omprpc import OmpProcessError
from band.runtime.tools import AgentTools
from tests.adapters.hold import Hold
from tests.adapters.lifecycle.runtime import ReplyingPlatform
from tests.adapters.omp.kit import run_turn
from tests.adapters.omp.lifecyclekit import HeldWaiterMailbox
from tests.adapters.omp.mailboxkit import mailbox_adapter, room_client
from tests.adapters.omp.mailboxpeer import (
    MODEL_PROVIDER,
    ORIGINAL_MODEL,
    REJECTED_MODEL,
    PeerCommand,
    model_reply,
)
from tests.integrations.omprpc.kit import owned_rpc_process
from tests.runtime.helpers import ALICE


class TestSelectionAdmission:
    @pytest.mark.parametrize(
        ("before_submit", "preceding_jobs", "outcome", "replies"),
        [
            (False, 0, nullcontext(), 2),
            (False, 1, pytest.raises(RuntimeError, match="cleanup pending"), 1),
            (True, 2, pytest.raises(OmpProcessError), 1),
        ],
        ids=[
            "before-lease-recovers",
            "leased-prepare-fails-closed",
            "admission-fails-closed",
        ],
    )
    async def test_interruption_before_prompt_preserves_the_accepted_model(
        self,
        tmp_path: Path,
        tools: AgentTools,
        platform: ReplyingPlatform,
        before_submit: bool,
        preceding_jobs: int,
        outcome: AbstractContextManager[object],
        replies: int,
    ) -> None:
        adapter = mailbox_adapter(tmp_path, callback_count=0)
        mailbox = HeldWaiterMailbox(before_submit=before_submit)
        adapter._mailbox = mailbox
        hold = Hold()
        turn: asyncio.Task[None] | None = None
        selecting: asyncio.Task[None] | None = None
        try:
            await run_turn(adapter, tools)
            client = room_client(adapter)
            process = owned_rpc_process(client)
            await client.request(
                PeerCommand.HOLD_MODEL_RESPONSE, modelId=REJECTED_MODEL
            )
            mailbox.waiters.extend([None] * preceding_jobs + [hold])
            current_tools = AgentTools(tools.room_id, tools.rest, participants=[ALICE])
            turn = asyncio.create_task(
                run_turn(adapter, current_tools, bootstrap=False)
            )
            await hold.arrived()
            selecting = asyncio.create_task(
                adapter.apply_model_selection(
                    ModelSelection(model=f"{MODEL_PROVIDER}/{REJECTED_MODEL}"),
                    room_id=tools.room_id,
                )
            )
            await client.request(PeerCommand.WAIT_MODEL_RESPONSE)
            selecting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await selecting
            hold.released.set()
            with outcome:
                await turn
            assert process.returncode is not None
            assert platform.replies == [model_reply(ORIGINAL_MODEL)] * replies

            follow_up = AgentTools(tools.room_id, tools.rest, participants=[ALICE])
            await run_turn(adapter, follow_up)
            assert platform.replies == [model_reply(ORIGINAL_MODEL)] * (replies + 1)
        finally:
            hold.released.set()
            tasks = [task for task in (turn, selecting) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await adapter.cleanup_all()
