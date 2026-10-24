"""A fetch_room_context failure clears replay and continues the turn."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.converters.omp import OmpHistoryConverter
from tests.adapters.omp.kit import FetchBoomTools, RecordingRuntime
from tests.integrations.acp.conftest import make_platform_message


class TestReplayFetchFailure:
    @pytest.mark.asyncio
    async def test_off_bootstrap_fetch_failure_continues_without_replay(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = RecordingRuntime()
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        msg = make_platform_message("Finish the background work")
        tools = FetchBoomTools()
        try:
            await adapter.run_judged_turn(
                msg=msg,
                tools=tools,
                history=OmpHistoryConverter().convert([]),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )
            assert "Conversation History" not in runtime.prompt_text
            assert tools.turn.replied
        finally:
            await adapter.cleanup_all()
