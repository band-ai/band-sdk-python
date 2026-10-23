"""xdist worker ids scope per-process resources that must not be shared."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import settings as hypothesis_settings
from hypothesis.database import DirectoryBasedExampleDatabase

from tests.xdistworker import (
    SERIAL_WORKER_ID,
    hypothesis_database_dir,
    worker_scoped,
    xdist_worker_id,
)


class TestXdistWorkerScope:
    def test_serial_runs_use_the_controller_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
        assert xdist_worker_id() == SERIAL_WORKER_ID

    def test_worker_scoped_names_differ_across_workers(self) -> None:
        assert worker_scoped("cache", "gw0") != worker_scoped("cache", "gw1")

    def test_hypothesis_database_dirs_differ_across_workers(self) -> None:
        gw0 = hypothesis_database_dir("gw0")
        gw1 = hypothesis_database_dir("gw1")
        assert gw0 != gw1
        assert gw0.name.endswith("-gw0")
        assert gw1.name.endswith("-gw1")

    def test_loaded_hypothesis_profile_writes_a_worker_database(self) -> None:
        database = hypothesis_settings().database
        assert isinstance(database, DirectoryBasedExampleDatabase)
        assert Path(database.path).resolve() == hypothesis_database_dir().resolve()
