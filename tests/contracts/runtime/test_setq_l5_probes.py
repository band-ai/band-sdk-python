from __future__ import annotations

from unittest.mock import patch

import pytest

from remote_agent.invocations import InvocationUnresolved
from tests.contracts.runtime.setp15 import capture
from tests.contracts.runtime.setql5probes import TransientProbeFixture


@pytest.mark.contract_linux
class TestTransientProbe(TransientProbeFixture):
    def test_more_than_eight_timeouts_survive_restart_then_success_is_cached(
        self,
    ) -> None:
        for _ in range(6):
            self.run_probe(self.timeout)
        retained = self.store.records()
        self.store.close()
        self.open_store()
        for _ in range(5):
            self.run_probe(self.timeout)
        self.run_probe(self.success)
        records = self.store.records()
        self.assertEqual(
            records[: len(retained)], retained, "retry overwrote timeout evidence"
        )
        inputs = [row["data"] for row in records if row["kind"] == "program_input"]
        self.assertEqual(len(inputs), 12)
        self.assertEqual(len({row["id"] for row in inputs}), 12)
        with patch.object(
            self.program,
            "execute",
            side_effect=AssertionError("cached success resampled"),
        ):
            self.assertEqual(self.journal.run(self.program, self.request), self.success)
        self.assertEqual(self.store.records(), records)

    def test_timeouts_leave_all_eight_deterministic_attempts_available(self) -> None:
        for _ in range(9):
            self.run_probe(self.timeout)
        for _ in range(8):
            self.run_probe(capture({"error": "deterministic callback failure"}))
        retained = self.store.records()
        self.store.close()
        self.open_store()
        with (
            patch.object(
                self.program,
                "execute",
                side_effect=AssertionError("exhausted probe ran"),
            ),
            self.assertRaisesRegex(InvocationUnresolved, "probe retry limit"),
        ):
            self.journal.run(self.program, self.request)
        self.assertEqual(self.store.records(), retained)
