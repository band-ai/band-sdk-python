"""A fetch_room_context failure clears replay and continues the turn."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapterConfig
from band.converters.omp import OmpHistoryConverter
from band.testing import FakeAgentTools
from tests.adapters.omp.custody import owned_room
from tests.adapters.omp.runtime import AdapterFactory
from tests.integrations.acp.conftest import make_platform_message
from tests.integrations.omprpc.wirelog import sent_prompt_messages


class TestReplayFetchFailure:
    @pytest.mark.asyncio
    async def test_off_bootstrap_fetch_failure_continues_without_replay(
        self, tmp_path: Path, real_adapter: AdapterFactory
    ):
        adapter = real_adapter(config=OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        msg = make_platform_message("Finish the background work")
        tools = FakeAgentTools()
        tools.fetch_room_context_error = RuntimeError("context unavailable")
        await adapter.run_judged_turn(
            msg=msg,
            tools=tools,
            history=OmpHistoryConverter().convert([]),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-123",
        )
        assert "Conversation History" not in "\n".join(
            await sent_prompt_messages(owned_room(adapter, "room-123").client)
        )
        assert tools.turn.replied
