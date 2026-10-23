"""Off-bootstrap replay pages through the full room context."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.converters.omp import OmpHistoryConverter
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import RecordingRuntime
from tests.integrations.acp.conftest import make_platform_message


class TestReplayPages:
    @pytest.mark.asyncio
    async def test_off_bootstrap_replay_includes_recent_pages(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = RecordingRuntime()
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        msg = make_platform_message("Finish the background work")
        older = [
            {
                "id": f"old-{index}",
                "content": f"old {index}",
                "sender_id": "user-1",
                "sender_type": "User",
                "sender_name": "Alice",
                "message_type": "text",
            }
            for index in range(50)
        ]
        recent = {
            "id": "recent",
            "content": "UNIQUE_RECENT",
            "sender_id": "user-1",
            "sender_type": "User",
            "sender_name": "Alice",
            "message_type": "text",
        }
        trigger = {
            "id": msg.id,
            "content": msg.content,
            "sender_id": msg.sender_id,
            "sender_type": msg.sender_type,
            "sender_name": msg.sender_name,
            "message_type": "text",
        }
        tools = FakeAgentTools(room_context=[*older, recent, trigger])
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
            assert "UNIQUE_RECENT" in runtime.prompt_text
        finally:
            await adapter.cleanup_all()
