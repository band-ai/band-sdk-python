"""Native model selection applies, restores the last selector, and fail-closes."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.core.model_catalog import ModelSelection
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import (
    MismatchThinkingRuntime,
    RecordingRuntime,
    RestoreFailRuntime,
    occupied_room_ids,
    requests_named,
    retained_selection,
    room_runtime,
    run_turn,
)


class TestModelSelection:
    @pytest.mark.asyncio
    async def test_rejected_effort_restores_the_running_model(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        runtime = RecordingRuntime()
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(
                cwd=str(tmp_path),
                model="openai/original",
                reasoning_effort="low",
            ),
            emit=(),
        )
        try:
            await run_turn(adapter, FakeAgentTools())
            with pytest.raises(ValueError, match="reasoning effort"):
                await adapter.apply_model_selection(
                    ModelSelection(
                        model="openai/replacement", reasoning_effort="extreme"
                    ),
                    room_id="room-123",
                )

            assert runtime.model == ("openai", "original")
            assert runtime.thinking == "low"
            assert retained_selection(adapter, "room-123") is None
        finally:
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_live_apply_sets_the_running_model(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        runtime = RecordingRuntime()
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), model="openai/original"),
            emit=(),
        )
        try:
            await run_turn(adapter, FakeAgentTools())
            await adapter.apply_model_selection(
                ModelSelection(model="openai/replacement", reasoning_effort="low"),
                room_id="room-123",
            )
            assert runtime.model == ("openai", "replacement")
            assert runtime.thinking == "low"
            assert requests_named(runtime, OmpCommandName.SET_MODEL)[-1] == {
                "provider": "openai",
                "modelId": "replacement",
            }
        finally:
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_invalid_model_is_rejected(self, monkeypatch, tmp_path: Path) -> None:
        runtime = RecordingRuntime()
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), model="openai/original"),
            emit=(),
        )
        try:
            await run_turn(adapter, FakeAgentTools())
            with pytest.raises(ValueError, match="provider/model"):
                await adapter.apply_model_selection(
                    ModelSelection(model="unqualified"),
                    room_id="room-123",
                )
            assert runtime.model == ("openai", "original")
        finally:
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_thinking_mismatch_is_rejected(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        runtime = MismatchThinkingRuntime()
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), model="openai/original"),
            emit=(),
        )
        try:
            await run_turn(adapter, FakeAgentTools())
            with pytest.raises(ValueError, match="did not apply"):
                await adapter.apply_model_selection(
                    ModelSelection(reasoning_effort="low"),
                    room_id="room-123",
                )
        finally:
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_failed_restore_retires_the_room(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        runtimes: list[RecordingRuntime] = []

        def spawn(*_args, **_kwargs) -> RecordingRuntime:
            runtime = RestoreFailRuntime() if not runtimes else RecordingRuntime()
            runtime.finish.set()
            runtimes.append(runtime)
            return runtime

        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", spawn)
        adapter = OmpAdapter(
            OmpAdapterConfig(
                cwd=str(tmp_path),
                model="openai/original",
                reasoning_effort="low",
            ),
            emit=(),
        )
        try:
            await run_turn(adapter, FakeAgentTools())
            retiring = runtimes[0]
            assert isinstance(retiring, RestoreFailRuntime)
            retiring.fail_restore_model = True
            with pytest.raises(ValueError, match="reasoning effort"):
                await adapter.apply_model_selection(
                    ModelSelection(
                        model="openai/replacement", reasoning_effort="extreme"
                    ),
                    room_id="room-123",
                )
            assert room_runtime(adapter, "room-123") is retiring
            await run_turn(adapter, FakeAgentTools())
            assert room_runtime(adapter, "room-123") is runtimes[-1]
            assert occupied_room_ids(adapter) == {"room-123"}
        finally:
            await adapter.cleanup_all()
