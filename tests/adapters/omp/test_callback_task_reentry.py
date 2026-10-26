"""RPC reader callbacks select a live model through the production mailbox."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter
from band.core.model_catalog import ModelSelection
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools, failure_reports
from tests.adapters.omp.custody import owned_room
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
from tests.adapters.omp.models import SelectingResolver
from tests.adapters.omp.turns import run_turn


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

    async def test_permission_apply_on_callback_task_updates_the_live_model(
        self, selection_adapter: tuple[OmpAdapter, SelectingResolver]
    ) -> None:
        adapter, resolver = selection_adapter
        tools = FakeAgentTools()
        await run_turn(adapter, tools)
        assert resolver.called.is_set()
        assert tools.turn.replied
        state = await owned_room(adapter, "room-123").client.request(
            OmpCommandName.GET_STATE
        )
        assert state["model"] == {"provider": "openai", "id": "other"}
        assert state["thinkingLevel"] == "low"

    async def test_later_turn_reader_callback_updates_the_live_model(
        self, selection_adapter: tuple[OmpAdapter, SelectingResolver]
    ) -> None:
        adapter, resolver = selection_adapter
        await run_turn(adapter, FakeAgentTools())
        resolver.called.clear()
        resolver.selection = ModelSelection(
            model="openai/later", reasoning_effort="high"
        )

        tools = FakeAgentTools()
        await run_turn(adapter, tools, bootstrap=False)

        assert resolver.called.is_set()
        assert tools.turn.replied
        state = await owned_room(adapter, "room-123").client.request(
            OmpCommandName.GET_STATE
        )
        assert state["model"] == {"provider": "openai", "id": "later"}
        assert state["thinkingLevel"] == "high"
