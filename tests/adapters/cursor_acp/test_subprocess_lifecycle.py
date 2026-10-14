"""Cursor decision and setup ownership through a real spawned ACP process."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import cast

import pytest

from band.client.streaming import ControlMode
from band.core.retention import Residency
from band.integrations.acp.cursor import DECISION_NOT_PENDING_TEMPLATE
from band.testing import failure_reports
from tests.adapters.cursor_acp.kit import permission_requests
from tests.adapters.cursor_acp.process import (
    CursorProcess,
    RecoveryClose,
    allow_write,
    asked_question,
    continue_question,
)
from tests.adapters.hold import Hold
from tests.adapters.lifecycle.kit import workspace_still_claimed
from tests.adapters.roompeer import (
    CLEANUP_FAILURE,
    WORKSPACE_NOTE,
    PeerCommand,
    read_reply,
    write_instruction,
)
from tests.adapters.waiting import until
from tests.integrations.acp.acp_toolkit.harness import decision_token
from tests.integrations.acp.acp_toolkit.lifecycle import LifecycleChild
from tests.integrations.acp.acp_toolkit.workspacepeer import (
    CURSOR_SETUP_HOLD,
    CURSOR_SETUP_READY,
    CursorChoice,
    CursorPeerEvent,
    cursor_workflow,
)


class TestSubprocessLifecycle:
    @pytest.mark.parametrize("cleanup", list(RecoveryClose))
    async def test_a_parked_decision_loses_authority_with_its_process(
        self, cursor_process: CursorProcess, cleanup: RecoveryClose
    ) -> None:
        process = cursor_process
        process.host.shared = True
        old = await process.send(cursor_workflow("obsolete"))
        old_token = await asked_question(old)
        process.host.shared = False
        other = await process.send(write_instruction("independent"), room="room-b")
        assert other.chat == ["independent"]
        assert not (process.workspace() / WORKSPACE_NOTE).exists()

        retained = process.host.bridge.residency("room-a")
        assert retained is not None
        child = cast(LifecycleChild, process.host.children[0])
        if cleanup == RecoveryClose.REFUSED_JOIN:
            child.closing = Hold()
            child.refuse_close = True
        await process.kill_child(0)
        await process.finished()
        if cleanup == RecoveryClose.REFUSED_JOIN:
            assert child.closing is not None
            await child.closing.arrived()
            joining = asyncio.create_task(process.send(cursor_workflow("premature")))
            await until(
                lambda: retained.snapshot.accepted == 1,
                failure="The successor never joined the held recovery close",
            )
            child.closing.released.set()
            with pytest.raises(RuntimeError, match=CLEANUP_FAILURE):
                await joining
            assert retained.snapshot.state == Residency.CLEANUP_PENDING
            assert retained.snapshot.pending_cleanup
            assert process.host.exited == [True, False]
            process.host.shared = True
            try:
                with workspace_still_claimed():
                    await process.send(write_instruction("transfer"), room="room-c")
            finally:
                process.host.shared = False
            child.refuse_close = False
        else:
            await process.host.settled("room-a")
        successor = await process.send(cursor_workflow("approved"))
        token = await asked_question(successor)
        late = await process.send(continue_question(old_token))

        assert late.chat == [DECISION_NOT_PENDING_TEMPLATE.format(token=old_token)]
        assert token != old_token
        assert process.host.exited == [True, False, False]
        assert len(failure_reports(old)) == 1
        assert process.observed(CursorPeerEvent.QUESTION_ANSWERED) == []
        assert process.observed(CursorPeerEvent.FILE_WRITTEN) == []

        await process.send(continue_question(token))
        await until(
            lambda: bool(permission_requests(successor)),
            failure="Cursor never requested write permission",
        )
        [permission] = permission_requests(successor)
        assert not (process.workspace() / WORKSPACE_NOTE).exists()
        await process.send(allow_write(decision_token(permission)))
        await process.finished()

        assert process.observed(CursorPeerEvent.QUESTION_ANSWERED) == [
            CursorChoice.CONTINUE
        ]
        assert process.observed(CursorPeerEvent.FILE_WRITTEN) == ["approved"]
        assert (process.workspace() / WORKSPACE_NOTE).read_text() == "approved"
        assert successor.chat[-1] == "approved"
        assert successor.reply.errors == []
        assert all("approved" in plan for plan in successor.reply.plans)
        assert any("completed" in plan for plan in successor.reply.plans)
        assert other.reply.plans == []
        assert await process.host.send("room-b", PeerCommand.READ) == [
            read_reply("independent")
        ]
        assert process.host.overlaps == 0
        if cleanup == RecoveryClose.REFUSED_JOIN:
            assert child.exited
            assert child.process.stdout is not None
            assert child.process.stdout.at_eof()
            # The refused exit leaves a test-owned context whose dead pipe
            # may report connection loss again after confirmed stdout EOF.
            with suppress(ConnectionError):
                await child.close()
            assert child.exited
            assert child.process.stdin.is_closing()

    async def test_interrupting_an_allocated_session_never_prompts_and_finally_reaps_it(
        self, cursor_process: CursorProcess
    ) -> None:
        process = cursor_process
        process.host.shared = True
        workspace = process.workspace()
        workspace.mkdir(parents=True)
        hold = workspace / CURSOR_SETUP_HOLD
        hold.touch()
        opening = asyncio.create_task(process.send(write_instruction("cancelled")))
        try:
            await until(
                (workspace / CURSOR_SETUP_READY).exists,
                failure="The peer never allocated its held session",
            )
            assert len(process.observed(CursorPeerEvent.SESSION_CREATED)) == 1
            await process.adapter.on_interrupt("room-a", ControlMode.STOP)
        finally:
            hold.unlink(missing_ok=True)
        with pytest.raises(asyncio.CancelledError):
            await opening
        await process.finished()

        assert process.observed(CursorPeerEvent.PROMPT_STARTED) == []
        assert not (workspace / WORKSPACE_NOTE).exists()
        healthy = await process.send(write_instruction("healthy"))
        await process.finished()
        assert healthy.chat == ["healthy"]
        assert healthy.reply.errors == []
        [prompt] = process.observed(CursorPeerEvent.PROMPT_STARTED)
        assert write_instruction("healthy") in prompt
        assert process.host.overlaps == 0

        await process.host.leave("room-a")
        assert all(process.host.exited)
        assert process.host.bridge.residency("room-a") is None
        assert await process.host.send("room-b", PeerCommand.READ) == [
            read_reply("healthy")
        ]
        await process.host.leave("room-b")
        assert all(process.host.exited)
