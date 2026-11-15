"""Duration maps merge across xdist workers and drop stale nodeids when cleaned."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.paths import REPO_ROOT
from tests.testdurations import (
    CLEAN_FLAG,
    DURATIONS_PATH_NAME,
    PATH_FLAG,
    STORE_FLAG,
    load_durations,
    quantize_duration,
    session_xdist_worker_id,
    worker_duration_path,
    write_durations,
)
from tests.xdistworker import SERIAL_WORKER_ID


class TestDurationStore:
    def test_quantize_buckets_sub_quantum_jitter(self) -> None:
        assert quantize_duration(1.04) == quantize_duration(1.0)
        assert quantize_duration(1.16) != quantize_duration(1.0)

    def test_leaked_parent_worker_env_is_not_this_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")
        assert session_xdist_worker_id(SimpleNamespace()) is None

    def test_this_session_workerinput_is_the_store_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")
        config = SimpleNamespace(workerinput={"workerid": "gw2"})
        assert session_xdist_worker_id(config) == "gw2"

    def test_controller_workerinput_does_not_write_a_sidecar(self) -> None:
        config = SimpleNamespace(workerinput={"workerid": SERIAL_WORKER_ID})
        assert session_xdist_worker_id(config) is None

    def test_controller_merges_worker_sidecars_and_drops_them(
        self, pytester: pytest.Pytester, tmp_path: Path
    ) -> None:
        pytester.syspathinsert(str(REPO_ROOT))
        pytester.makepyfile("def test_ok(): assert True")
        path = tmp_path / DURATIONS_PATH_NAME
        write_durations(path, {"gone.py::stale": 9.0})
        write_durations(worker_duration_path(path, "gw0"), {"other.py::test_a": 1.04})
        write_durations(worker_duration_path(path, "gw1"), {"other.py::test_b": 2.16})
        result = pytester.runpytest(
            "-p",
            "tests.testdurations",
            STORE_FLAG,
            CLEAN_FLAG,
            f"{PATH_FLAG}={path}",
            "-q",
        )
        result.assert_outcomes(passed=1)
        stored = json.loads(path.read_text(encoding="utf-8"))
        assert "gone.py::stale" not in stored
        assert stored["other.py::test_a"] == quantize_duration(1.04)
        assert stored["other.py::test_b"] == quantize_duration(2.16)
        assert any(nodeid.endswith("test_ok") for nodeid in stored)
        assert not tuple(path.parent.glob(f"{path.name}.gw*"))

    def test_load_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert load_durations(tmp_path / DURATIONS_PATH_NAME) == {}

    def test_load_rejects_non_object_json(self, tmp_path: Path) -> None:
        path = tmp_path / DURATIONS_PATH_NAME
        path.write_text("[]\n", encoding="utf-8")
        assert load_durations(path) == {}
