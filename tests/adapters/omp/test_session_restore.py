"""Persisted native sessions resume, or fall back to a fresh session and replay."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter
from band.converters.omp import OmpSessionState
from band.integrations.omprpc import OmpRpcError
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import (
    RecordingRuntime,
    native_session_file,
    requests_named,
    run_turn,
)


class TestSessionRestore:
    @pytest.mark.asyncio
    async def test_persisted_session_resumes_without_replaying_history(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        runtime = RecordingRuntime()
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        saved = native_session_file(tmp_path)
        adapter = OmpAdapter(workspace_for_room=lambda _room: str(tmp_path), emit=())
        history = OmpSessionState(
            room_to_session={"room-123": str(saved)},
            replay_messages=["[Alice]: earlier"],
        )
        try:
            await run_turn(adapter, FakeAgentTools(), history=history)

            assert requests_named(runtime, "switch_session") == [
                {"sessionPath": str(saved)}
            ]
            assert requests_named(runtime, "new_session") == []
            assert adapter._rooms["room-123"].session_file == str(saved)
            assert "Conversation History" not in runtime.prompt_text
            assert "[Alice]: earlier" not in runtime.prompt_text
        finally:
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_failed_restore_starts_fresh_and_replays_history(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        runtime = RecordingRuntime()
        runtime.finish.set()
        runtime.switch_error = OmpRpcError("switch_session", "session missing")
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        saved = native_session_file(tmp_path)
        adapter = OmpAdapter(workspace_for_room=lambda _room: str(tmp_path), emit=())
        history = OmpSessionState(
            room_to_session={"room-123": str(saved)},
            replay_messages=["[Alice]: earlier"],
        )
        try:
            await run_turn(adapter, FakeAgentTools(), history=history)

            assert requests_named(runtime, "switch_session") == [
                {"sessionPath": str(saved)}
            ]
            assert requests_named(runtime, "new_session") == [{}]
            assert adapter._rooms["room-123"].session_file is None
            assert "[Alice]: earlier" in runtime.prompt_text
        finally:
            await adapter.cleanup_all()
