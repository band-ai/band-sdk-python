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
    merge_duration_maps,
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

    def test_clean_merge_drops_stale_nodeids(self) -> None:
        merged = merge_duration_maps(
            {"tests/new.py::test_ok": 1.0},
            existing={"tests/gone.py::test_old": 9.0},
            clean=True,
        )
        assert merged == {"tests/new.py::test_ok": 1.0}

    def test_keep_merge_retains_unseen_nodeids(self) -> None:
        merged = merge_duration_maps(
            {"tests/new.py::test_ok": 1.0},
            existing={"tests/gone.py::test_old": 9.0},
            clean=False,
        )
        assert merged == {
            "tests/gone.py::test_old": 9.0,
            "tests/new.py::test_ok": 1.0,
        }

    def test_worker_paths_are_unique(self) -> None:
        path = Path(DURATIONS_PATH_NAME)
        assert worker_duration_path(path, "gw0") != worker_duration_path(path, "gw1")

    def test_leaked_parent_worker_env_is_not_this_session(self) -> None:
        assert session_xdist_worker_id(SimpleNamespace()) is None

    def test_this_session_workerinput_is_the_store_key(self) -> None:
        config = SimpleNamespace(workerinput={"workerid": "gw2"})
        assert session_xdist_worker_id(config) == "gw2"

    def test_controller_workerinput_does_not_write_a_sidecar(self) -> None:
        config = SimpleNamespace(workerinput={"workerid": SERIAL_WORKER_ID})
        assert session_xdist_worker_id(config) is None

    def test_non_dict_workerinput_is_serial(self) -> None:
        assert session_xdist_worker_id(SimpleNamespace(workerinput="gw0")) is None

    def test_store_option_writes_quantized_json(
        self, pytester: pytest.Pytester, tmp_path: Path
    ) -> None:
        pytester.syspathinsert(str(REPO_ROOT))
        pytester.makepyfile("def test_ok(): assert True")
        path = tmp_path / DURATIONS_PATH_NAME
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
        assert len(stored) == 1
        nodeid, seconds = next(iter(stored.items()))
        assert nodeid.endswith("test_ok")
        assert seconds == quantize_duration(seconds)
        assert not tuple(path.parent.glob(f"{path.name}.gw*"))

    def test_load_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert load_durations(tmp_path / DURATIONS_PATH_NAME) == {}

    def test_load_rejects_non_object_json(self, tmp_path: Path) -> None:
        path = tmp_path / DURATIONS_PATH_NAME
        path.write_text("[]\n", encoding="utf-8")
        assert load_durations(path) == {}

    def test_write_roundtrip_is_stable(self, tmp_path: Path) -> None:
        path = tmp_path / DURATIONS_PATH_NAME
        write_durations(path, {"b": 2.0, "a": 1.0})
        assert path.read_text(encoding="utf-8") == ('{\n  "a": 1.0,\n  "b": 2.0\n}\n')
