"""Persisted native sessions resume, or fall back to a fresh session and replay."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter
from band.converters.omp import OmpSessionState
from band.integrations.omprpc import OmpRpcError
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import (
    RecordingRuntime,
    native_session_file,
    requests_named,
    room_runtime,
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

            assert requests_named(runtime, OmpCommandName.SWITCH_SESSION) == [
                {"sessionPath": str(saved)}
            ]
            assert requests_named(runtime, OmpCommandName.NEW_SESSION) == []
            assert runtime.session_file == str(saved)
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
        runtime.switch_error = OmpRpcError(
            OmpCommandName.SWITCH_SESSION, "session missing"
        )
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        saved = native_session_file(tmp_path)
        adapter = OmpAdapter(workspace_for_room=lambda _room: str(tmp_path), emit=())
        history = OmpSessionState(
            room_to_session={"room-123": str(saved)},
            replay_messages=["[Alice]: earlier"],
        )
        try:
            await run_turn(adapter, FakeAgentTools(), history=history)

            assert requests_named(runtime, OmpCommandName.SWITCH_SESSION) == [
                {"sessionPath": str(saved)}
            ]
            assert requests_named(runtime, OmpCommandName.NEW_SESSION) == [{}]
            assert runtime.session_file is None
            assert "[Alice]: earlier" in runtime.prompt_text
        finally:
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_cancelled_switch_replays_without_new_session(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        runtime = RecordingRuntime()
        runtime.finish.set()
        runtime.switch_cancelled = True
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        saved = native_session_file(tmp_path)
        adapter = OmpAdapter(workspace_for_room=lambda _room: str(tmp_path), emit=())
        history = OmpSessionState(
            room_to_session={"room-123": str(saved)},
            replay_messages=["[Alice]: earlier"],
        )
        try:
            await run_turn(adapter, FakeAgentTools(), history=history)
            assert requests_named(runtime, OmpCommandName.NEW_SESSION) == []
            assert "[Alice]: earlier" in runtime.prompt_text
        finally:
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_cancelled_new_session_fails_closed(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        runtime = RecordingRuntime()
        runtime.finish.set()
        runtime.switch_error = OmpRpcError(
            OmpCommandName.SWITCH_SESSION, "session missing"
        )
        runtime.new_session_cancelled = True
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        saved = native_session_file(tmp_path)
        adapter = OmpAdapter(workspace_for_room=lambda _room: str(tmp_path), emit=())
        history = OmpSessionState(
            room_to_session={"room-123": str(saved)},
            replay_messages=["[Alice]: earlier"],
        )
        with pytest.raises(RuntimeError, match="replacement session"):
            await run_turn(adapter, FakeAgentTools(), history=history)
        await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_foreign_session_path_is_not_restored(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        runtime = RecordingRuntime()
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        foreign = tmp_path / "outside.jsonl"
        foreign.write_text("{}", encoding="utf-8")
        adapter = OmpAdapter(workspace_for_room=lambda _room: str(tmp_path), emit=())
        history = OmpSessionState(
            room_to_session={"room-123": str(foreign)},
            replay_messages=["[Alice]: earlier"],
        )
        try:
            await run_turn(adapter, FakeAgentTools(), history=history)
            assert requests_named(runtime, OmpCommandName.SWITCH_SESSION) == []
            assert "[Alice]: earlier" in runtime.prompt_text
            assert room_runtime(adapter, "room-123") is runtime
        finally:
            await adapter.cleanup_all()
