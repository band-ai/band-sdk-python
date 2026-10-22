from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from remote_agent.invocations import InvocationJournal, InvocationUnresolved
from remote_agent.store import Store
from tests.contracts.runtime.invocations.kit import InterruptedHost, ProbeRetryFixture


@pytest.mark.contract_linux
class TestProbeRetry(ProbeRetryFixture):
    def test_failed_probes_retry_fresh_suffix_and_reuse_only_the_success(self) -> None:
        failures = [
            {"exit_code": None, "failure": "Callback timeout", "complete": False},
            {"exit_code": 7, "failure": None, "complete": True},
            {"exit_code": 0, "failure": "sandbox unavailable", "complete": True},
            {"exit_code": 0, "failure": None, "complete": False},
        ]
        for execution in failures:
            with patch.object(
                self.program, "execute", return_value=execution
            ) as execute:
                self.assertEqual(
                    self.journal.run(self.program, self.request), execution
                )
                execute.assert_called_once()
        with patch.object(
            self.program, "execute", return_value=self.success
        ) as execute:
            self.assertEqual(self.journal.run(self.program, self.request), self.success)
            execute.assert_called_once()
        inputs = self.inputs()
        self.assertEqual(len(inputs), len(failures) + 1)
        self.assertEqual(len({r["id"] for r in inputs}), len(inputs))
        for record, execution in zip(inputs, failures + [self.success]):
            self.assertEqual(
                self.journal.recorded(record["id"])["execution"], execution
            )
        records = self.store.records()
        with patch.object(
            self.program,
            "execute",
            side_effect=AssertionError("successful probe re-executed"),
        ):
            self.assertEqual(self.journal.run(self.program, self.request), self.success)
        self.assertEqual(self.store.records(), records)

    def test_started_probes_retry_after_restart_without_overwriting_old_attempts(
        self,
    ) -> None:
        for _ in range(2):
            with (
                patch.object(self.program, "execute", side_effect=InterruptedHost),
                self.assertRaises(InterruptedHost),
            ):
                self.journal.run(self.program, self.request)
        old = self.inputs()
        self.store.close()
        self.store = Store(os.path.join(self.temp.name, "state.sqlite3"))
        self.addCleanup(self.store.close)
        self.journal = InvocationJournal(self.store)
        self.assertEqual(self.journal.run(self.program, self.request), self.success)
        self.assertEqual(len(self.inputs()), 3)
        for record in old:
            self.assertEqual(self.journal.recorded(record["id"])["status"], "started")
        records = self.store.records()
        with patch.object(
            self.program,
            "execute",
            side_effect=AssertionError("successful retry re-executed"),
        ):
            self.assertEqual(self.journal.run(self.program, self.request), self.success)
        self.assertEqual(self.store.records(), records)

    def test_explicit_live_ids_still_hold_or_replay_failed_results(self) -> None:
        with (
            patch.object(self.program, "execute", side_effect=InterruptedHost),
            self.assertRaises(InterruptedHost),
        ):
            self.journal.run(
                self.program, self.request, invocation_id="live", purpose="turn"
            )
        records = self.store.records()
        with (
            patch.object(
                self.program,
                "execute",
                side_effect=AssertionError("live turn resampled"),
            ),
            self.assertRaises(InvocationUnresolved),
        ):
            self.journal.run(
                self.program, self.request, invocation_id="live", purpose="turn"
            )
        self.assertEqual(self.store.records(), records)
        failure = {"exit_code": 1, "failure": "failed live turn", "complete": True}
        with patch.object(self.program, "execute", return_value=failure):
            self.journal.run(
                self.program, self.request, invocation_id="failed", purpose="turn"
            )
        with patch.object(
            self.program,
            "execute",
            side_effect=AssertionError("live failure resampled"),
        ):
            self.assertEqual(
                self.journal.run(
                    self.program, self.request, invocation_id="failed", purpose="turn"
                ),
                failure,
            )
