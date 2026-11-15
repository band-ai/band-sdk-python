"""A successful turn without a session file does not tombstone resume."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OMP_SESSION_DIRECTORY, OmpAdapterConfig
from band.converters.omp import OMP_RESUME_RECORD, OmpHistoryConverter, OmpMetadataKey
from band.core.types import MessageType
from band.testing import FakeAgentTools
from band.testing.fake_tools import events_of_type
from tests.adapters.omp.runtime import AdapterFactory
from tests.adapters.omp.sessions import native_session_file
from tests.adapters.omp.turns import run_turn


class TestSessionPersist:
    @pytest.mark.asyncio
    async def test_successful_turn_without_session_file_keeps_resume(
        self, tmp_path: Path, real_adapter: AdapterFactory
    ):
        adapter = real_adapter(config=OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        prior = [
            {
                "message_type": MessageType.TASK,
                "content": OMP_RESUME_RECORD.label,
                "metadata": {
                    OmpMetadataKey.ROOM: "room-123",
                    OmpMetadataKey.SESSION: f"/workspace/{OMP_SESSION_DIRECTORY}/saved.jsonl",
                },
            }
        ]
        tools = FakeAgentTools()
        await run_turn(adapter, tools)
        posted = events_of_type(tools, MessageType.TASK)
        assert posted
        assert OmpMetadataKey.SESSION not in posted[-1]["metadata"]
        state = OmpHistoryConverter().convert([*prior, *posted])
        assert (
            state.room_to_session["room-123"]
            == f"/workspace/{OMP_SESSION_DIRECTORY}/saved.jsonl"
        )

    @pytest.mark.asyncio
    async def test_successful_turn_posts_session_file(
        self, tmp_path: Path, real_adapter: AdapterFactory
    ):
        session_file = str(native_session_file(tmp_path))
        adapter = real_adapter(
            session_file=session_file,
            config=OmpAdapterConfig(cwd=str(tmp_path)),
            emit=(),
        )
        tools = FakeAgentTools()
        await run_turn(adapter, tools)
        posted = events_of_type(tools, MessageType.TASK)
        assert posted
        assert posted[-1]["metadata"][OmpMetadataKey.SESSION] == session_file
