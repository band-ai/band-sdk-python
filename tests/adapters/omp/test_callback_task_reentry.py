"""RPC reader callbacks select a live model through the production mailbox."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.testing import failure_reports
from tests.adapters.omp.kit import run_turn
from tests.adapters.omp.mailboxkit import (
    CallbackResolver,
    MailboxTools,
    mailbox_adapter,
    peer_frames,
    selection_order,
)
from tests.adapters.omp.mailboxpeer import (
    CALLBACK_MODEL,
    FIRST_CALLBACK,
    ORIGINAL_MODEL,
    model_reply,
)


class TestCallbackTaskReentry:
    @pytest.mark.parametrize("callback_turn", [1, 2], ids=["first-turn", "later-turn"])
    async def test_permission_selection_precedes_response_and_survives_followup(
        self, tmp_path: Path, callback_turn: int
    ) -> None:
        resolver = CallbackResolver()
        adapter = mailbox_adapter(tmp_path, resolver, callback_turn=callback_turn)
        resolver.adapter = adapter
        tools = MailboxTools()
        try:
            for number in range(1, callback_turn + 1):
                tools = MailboxTools()
                await run_turn(adapter, tools, bootstrap=number == 1)
                assert failure_reports(tools) == []
            assert resolver.calls == [FIRST_CALLBACK]
            assert tools.chat == [model_reply(CALLBACK_MODEL)]
            assert selection_order(await peer_frames(adapter)) == [
                ORIGINAL_MODEL,
                CALLBACK_MODEL,
                FIRST_CALLBACK,
            ]

            follow_up = MailboxTools()
            await run_turn(adapter, follow_up, bootstrap=False)
            assert follow_up.chat == [model_reply(CALLBACK_MODEL)]
            assert failure_reports(follow_up) == []
        finally:
            await adapter.cleanup_all()
