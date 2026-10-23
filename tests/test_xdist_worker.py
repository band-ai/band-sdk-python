"""xdist worker ids scope per-process resources that must not be shared."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import settings as hypothesis_settings
from hypothesis.database import DirectoryBasedExampleDatabase

from tests.xdistworker import SERIAL_WORKER_ID, hypothesis_database_dir, xdist_worker_id


class TestXdistWorkerScope:
    def test_serial_runs_use_the_controller_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
        assert xdist_worker_id() == SERIAL_WORKER_ID

    def test_loaded_hypothesis_profile_writes_a_worker_database(self) -> None:
        database = hypothesis_settings().database
        assert isinstance(database, DirectoryBasedExampleDatabase)
        assert Path(database.path).resolve() == hypothesis_database_dir().resolve()
