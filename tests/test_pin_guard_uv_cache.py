"""Pin-guard uv cache directories must not be shared across xdist workers."""

from __future__ import annotations

import pytest

from tests.test_python_core_coverage_workflow import pin_guard_uv_cache_dir
from tests.xdistworker import SERIAL_WORKER_ID


class TestPinGuardUvCache:
    def test_serial_runs_scope_the_cache_to_the_controller(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
        assert pin_guard_uv_cache_dir().name.endswith(f"-{SERIAL_WORKER_ID}")

    def test_workers_get_distinct_cache_directories(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
        gw0 = pin_guard_uv_cache_dir()
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
        gw1 = pin_guard_uv_cache_dir()
        assert gw0 != gw1
        assert gw0.name.endswith("-gw0")
        assert gw1.name.endswith("-gw1")
