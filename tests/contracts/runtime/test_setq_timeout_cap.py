from __future__ import annotations

from unittest.mock import patch

import pytest

from remote_agent.invocations import InvocationUnresolved
from remote_agent.program import Artifact, Program
from tests.contracts.runtime.callbackdeadline import CALLBACK_LOOP, callback_deadline
from tests.contracts.runtime.program import SOURCE
from tests.contracts.runtime.setql5probes import TransientProbeFixture


class TestProbeTimeoutCap(TransientProbeFixture):
    @pytest.mark.contract_linux
    def test_sixteenth_timeout_exhausts_across_restart_without_execution_or_new_records(
        self,
    ) -> None:
        for _ in range(15):
            self.run_probe(self.timeout)
        self.store.close()
        self.open_store()
        self.run_probe(self.timeout)
        records = self.store.records()
        self.assertEqual(
            len([row for row in records if row["kind"] == "program_input"]), 16
        )
        self.store.close()
        self.open_store()
        for _ in range(3):
            with (
                patch.object(
                    self.program,
                    "execute",
                    side_effect=AssertionError("timeout-exhausted probe ran"),
                ),
                self.assertRaisesRegex(InvocationUnresolved, "timeout.*limit"),
            ):
                self.journal.run(self.program, self.request)
            self.assertEqual(self.store.records(), records)

    @pytest.mark.contract_linux
    def test_success_after_fifteen_timeouts_is_allowed_and_cached_across_restart(
        self,
    ) -> None:
        for _ in range(15):
            self.run_probe(self.timeout)
        self.run_probe(self.success)
        records = self.store.records()
        self.store.close()
        self.open_store()
        with patch.object(
            self.program,
            "execute",
            side_effect=AssertionError("successful probe resampled"),
        ):
            self.assertEqual(self.journal.run(self.program, self.request), self.success)
        self.assertEqual(self.store.records(), records)

    @pytest.mark.contract_isolation
    def test_real_pure_callback_loop_stops_after_sixteen_durable_timeouts(self) -> None:
        source = (
            SOURCE + "\ndef handle(event, state, context):\n    " + CALLBACK_LOOP + "\n"
        )
        program = Program(
            Artifact.publish(source, self.root / "loop-artifacts"), timeout=10
        )
        request = program.request("handle", event={}, state={}, view={})
        for _ in range(16):
            with callback_deadline() as deadline:
                execution = self.journal.run(program, request)
                self.assertTrue(deadline.entered and deadline.expired)
            self.assertEqual(execution["failure"], "Callback timeout")
            self.assertFalse(execution["complete"])
        records = self.store.records()
        self.assertEqual(
            len([row for row in records if row["kind"] == "program_output"]), 16
        )
        with (
            patch.object(
                program,
                "execute",
                side_effect=AssertionError("pure loop executed after ceiling"),
            ),
            self.assertRaisesRegex(InvocationUnresolved, "timeout.*limit"),
        ):
            self.journal.run(program, request)
        self.assertEqual(self.store.records(), records)
