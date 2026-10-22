"""Read retained boundary-depth callbacks through the real public operator CLI."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from band.contracts.journal import ContractJournal
from band.contracts.models import RuntimeConfig
from tests.contracts.journaldepth import record_callback, record_private_value


@pytest.mark.contract_runtime
class TestJournalDepth:
    @pytest.mark.parametrize("levels", [125, 126])
    async def test_public_journal_preserves_accepted_callback_state(
        self, tmp_path: Path, levels: int
    ) -> None:
        recording = record_callback(tmp_path, levels)
        operator = ContractJournal(
            recording.path, runtime=RuntimeConfig(python=Path(sys.executable))
        )

        assert await operator.journal() == recording.page

    async def test_public_journal_preserves_json_accepted_by_private_storage(
        self, tmp_path: Path
    ) -> None:
        recording = record_private_value(tmp_path)
        operator = ContractJournal(
            recording.path, runtime=RuntimeConfig(python=Path(sys.executable))
        )

        assert await operator.journal() == recording.page
