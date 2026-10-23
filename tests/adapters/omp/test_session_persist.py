"""A successful turn without a session file does not tombstone resume."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OMP_SESSION_DIRECTORY, OmpAdapter, OmpAdapterConfig
from band.converters.omp import OmpHistoryConverter, OmpMetadataKey
from band.core.types import MessageType
from band.testing import FakeAgentTools
from band.testing.fake_tools import events_of_type
from tests.adapters.omp.kit import RecordingRuntime, run_turn


class TestSessionPersist:
    @pytest.mark.asyncio
    async def test_successful_turn_without_session_file_keeps_resume(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = RecordingRuntime()
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        prior = [
            {
                "message_type": MessageType.TASK,
                "metadata": {
                    OmpMetadataKey.ROOM: "room-123",
                    OmpMetadataKey.SESSION: f"/workspace/{OMP_SESSION_DIRECTORY}/saved.jsonl",
                },
            }
        ]
        tools = FakeAgentTools()
        try:
            await run_turn(adapter, tools)
            posted = events_of_type(tools, MessageType.TASK)
            assert posted
            assert OmpMetadataKey.SESSION not in posted[-1]["metadata"]
            state = OmpHistoryConverter().convert([*prior, *posted])
            assert (
                state.room_to_session["room-123"]
                == f"/workspace/{OMP_SESSION_DIRECTORY}/saved.jsonl"
            )
        finally:
            await adapter.cleanup_all()
