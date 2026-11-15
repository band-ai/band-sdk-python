"""Native OMP reply ownership across background turns."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.core.protocols import TurnResultAlreadyReported
from band.testing import MISSING_REPLY_FAILURE, failure_reports
from tests.adapters.omp.mailboxkit import (
    CallbackResolver,
    MailboxTools,
    mailbox_adapter,
)
from tests.adapters.omp.mailboxpeer import CALLBACK_MODEL, model_reply
from tests.adapters.omp.turns import NativeTurnFactory, reply_call, run_turn
from tests.toolkit.tasks import background


class TestTurnOwnership:
    async def test_background_reply_keeps_original_turn_owned(
        self, native_turn: NativeTurnFactory
    ) -> None:
        async with native_turn(emit=()) as turn:
            assert failure_reports(turn.tools) == []
            assert not turn.tools.turn.replied
            await turn.callbacks.tool(reply_call("Background work finished"))

        assert turn.tools.turn.replied
        assert failure_reports(turn.tools) == []

    async def test_settled_without_terminal_tool_still_reports_failure(
        self, native_turn: NativeTurnFactory
    ) -> None:
        with pytest.raises(TurnResultAlreadyReported):
            async with native_turn(emit=()) as turn:
                assert not turn.tools.turn.replied

        assert failure_reports(turn.tools) == [MISSING_REPLY_FAILURE]
        assert not turn.tools.turn.replied

    async def test_cancelled_background_turn_cannot_own_next_reply(
        self, tmp_path: Path
    ) -> None:
        resolver = CallbackResolver(held=True)
        adapter = mailbox_adapter(tmp_path, resolver)
        resolver.adapter = adapter
        old_tools = MailboxTools()
        try:
            async with background(run_turn(adapter, old_tools)) as pending:
                await resolver.first.reached.wait()
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending

            resolver.first.released.set()
            new_tools = MailboxTools()
            await run_turn(adapter, new_tools, bootstrap=False)

            assert new_tools.chat == [model_reply(CALLBACK_MODEL)]
            assert old_tools.chat == []
            assert new_tools.turn.replied
            assert not old_tools.turn.replied
            assert failure_reports(old_tools) == []
            assert failure_reports(new_tools) == []
        finally:
            resolver.first.released.set()
            await adapter.cleanup_all()
