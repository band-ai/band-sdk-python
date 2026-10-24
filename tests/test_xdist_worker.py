"""xdist worker ids scope per-process resources that must not be shared."""

from __future__ import annotations

import os
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

    def test_local_parlant_servers_use_a_temporary_worker_home(self) -> None:
        home = Path(os.environ["PARLANT_HOME"])
        pytest.importorskip("parlant.sdk")
        from parlant.bin.server import PARLANT_HOME_DIR  # noqa: PLC0415

        assert home.is_dir()
        assert home.name.startswith(f"band-parlant-home-{xdist_worker_id()}-")
        assert PARLANT_HOME_DIR == home
