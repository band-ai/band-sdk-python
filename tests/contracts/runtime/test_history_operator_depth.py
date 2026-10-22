"""Preserve real model history through the installed public operator command."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from band.contracts.journal import ContractJournal
from band.contracts.models import RuntimeConfig
from tests.contracts.runtime.historydepth import HistoryDepthFixture, container_depth


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestHistoryOperatorDepth(HistoryDepthFixture):
    def test_public_journal_returns_the_complete_real_history_page(self) -> None:
        self.history_record()
        expected = self.store.search(kind="model_input")
        self.assertEqual(container_depth(expected), 140)
        operator = ContractJournal(
            self.store.path, runtime=RuntimeConfig(python=Path(sys.executable))
        )
        self.assertEqual(asyncio.run(operator.journal(kind="model_input")), expected)
        self.assertEqual(self.model.calls, ["wake", "respond", "respond"])

    def test_public_export_returns_the_retained_deep_anchor(self) -> None:
        self.retained_record()
        archive = self.path / "public-retained.jsonl"
        operator = ContractJournal(
            self.store.path, runtime=RuntimeConfig(python=Path(sys.executable))
        )
        header = asyncio.run(operator.export(archive))
        self.assertEqual(header["profile"], "remote-agent/journal-export/2")
        self.assertEqual(header["anchor"], self.store.anchor())
        self.assertGreater(container_depth(header), 128)
        with archive.open() as stream:
            self.assertEqual(json.loads(stream.readline()), header)
        self.assertEqual(self.model.calls, ["respond", "respond"])
