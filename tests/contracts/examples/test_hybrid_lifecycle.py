from __future__ import annotations

from pathlib import Path

import pytest

from tests.contracts.examples.kit import (
    CleanupFailure,
    EngineFactory,
    EngineRole,
    InitializationFailure,
    StartupFailure,
    run_demo,
)


class TestHybridLifecycle:
    def test_second_engine_start_failure_releases_the_ready_authority(
        self,
        engines: EngineFactory,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        engines.fail_checker_startup = True

        with pytest.raises(StartupFailure) as caught:
            run_demo(engines, monkeypatch, tmp_path / "workspace")

        assert caught.value is engines.startup_failure
        assert engines.start_attempts == [EngineRole.AUTHORITY, EngineRole.CHECKER]
        assert list(engines.acquired) == [EngineRole.AUTHORITY]
        assert engines.initialization_requests == []
        assert engines.all_released
        assert engines.close_attempts == [EngineRole.AUTHORITY]

    @pytest.mark.parametrize(
        "failing_engine", [EngineRole.CHECKER, EngineRole.AUTHORITY]
    )
    def test_cleanup_failure_does_not_strand_another_owned_engine(
        self,
        failing_engine: EngineRole,
        engines: EngineFactory,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        engines.failing_cleanup = failing_engine

        with pytest.raises(CleanupFailure) as caught:
            run_demo(engines, monkeypatch, tmp_path / "workspace")

        assert caught.value is engines.cleanup_failure
        assert caught.value.__context__ is engines.initialization_failure
        assert len(engines.initialization_requests) == 1
        assert engines.all_released
        assert engines.close_attempts == [EngineRole.CHECKER, EngineRole.AUTHORITY]

    def test_room_initialization_failure_is_preserved_after_cleanup(
        self,
        engines: EngineFactory,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        with pytest.raises(InitializationFailure) as caught:
            run_demo(engines, monkeypatch, tmp_path / "workspace")

        assert caught.value is engines.initialization_failure
        assert len(engines.initialization_requests) == 1
        assert engines.all_released
        assert engines.close_attempts == [EngineRole.CHECKER, EngineRole.AUTHORITY]
