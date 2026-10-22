from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from remote_agent.invocations import InvocationJournal, InvocationUnresolved
from remote_agent.store import Store
from tests.contracts.runtime.setp15 import ProbeBoundsFixture, capture


@pytest.mark.contract_linux
class TestProbeBounds(ProbeBoundsFixture):
    def test_exit_zero_worker_error_or_invalid_abi_is_retried_not_cached(self) -> None:
        for bad in (
            {"error": "deterministic failure"},
            {},
            {"schema": "1", "initial": {}},
        ):
            with self.subTest(bad=bad):
                request = dict(self.request, case=json.dumps(bad))
                with patch.object(self.program, "execute", return_value=capture(bad)):
                    self.journal.run(self.program, request)
                with patch.object(
                    self.program, "execute", return_value=self.success
                ) as execute:
                    self.assertEqual(
                        self.journal.run(self.program, request), self.success
                    )
                    execute.assert_called_once()
                before = self.store.records()
                with patch.object(
                    self.program,
                    "execute",
                    side_effect=AssertionError("valid probe repeated"),
                ):
                    self.assertEqual(
                        self.journal.run(self.program, request), self.success
                    )
                self.assertEqual(before, self.store.records())

    def test_deterministic_probe_retries_are_capped_across_store_restart(self) -> None:
        failure = {
            "exit_code": 7,
            "failure": "Callback exited with status 7",
            "complete": True,
        }
        request = dict(self.request, view={"large": "x" * 100000})
        for _ in range(8):
            with patch.object(self.program, "execute", return_value=failure):
                self.journal.run(self.program, request)
        before = self.store.records()
        self.store.close()
        self.store = Store(self.root / "state.sqlite3")
        self.addCleanup(self.store.close)
        self.journal = InvocationJournal(self.store)
        with (
            patch.object(
                self.program,
                "execute",
                side_effect=AssertionError("exhausted probe executed"),
            ),
            self.assertRaisesRegex(InvocationUnresolved, "probe.*limit"),
        ):
            self.journal.run(self.program, request)
        self.assertEqual(before, self.store.records())
        self.assertEqual(len([r for r in before if r["kind"] == "program_input"]), 8)
