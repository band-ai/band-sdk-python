"""Carry the public retention budget into the generic driver's idle sweep."""

from __future__ import annotations

from contextlib import closing
from unittest.mock import patch

import pytest

from band.contracts.programs import ProgramStart
from remote_agent.retention import logical_bytes
from tests.contracts.runtime.bandstandardsource import GenericStandardSourceFixture
from tests.contracts.runtime.required import require_value


@pytest.mark.contract_isolation
@pytest.mark.contract_engine("band")
class TestPublicStartRetention(GenericStandardSourceFixture):
    def test_public_budget_reaches_real_driver_and_prunes_to_its_step_floor(
        self,
    ) -> None:
        self.authority()
        start = ProgramStart(
            id="bounded-start",
            assessment_id="qualified",
            mode="program",
            journal=True,
            target_bytes=1_000_000,
            minimum_steps=3,
        )
        with closing(self.start(start.body())) as driver:
            driver.thread.join(1)
            self.assertFalse(driver.thread.is_alive())
            self.assertEqual(driver.options, start.body())
            store = driver.service.store
            program = store.get("program")["sha256"]
            schema = store.get("metadata")["schema"]
            # Seed completed-history pressure, not incoming business requests.
            # The initialized controller, idle loop and retention store are real.
            for step in range(1, 9):
                before = {"processed": step - 1, "mode": "ascii"}
                after = {"processed": step, "mode": "ascii"}
                store.commit(
                    "turn",
                    {
                        "input": {
                            "id": f"budget-{step}",
                            "payload": {"text": "x" * 160_000},
                        },
                        "input_at": step,
                        "before": before,
                        "state": after,
                        "program": program,
                        "producer": "program",
                        "message": None,
                        "view": {},
                    },
                    updates={"state": after, "turns": step},
                    snapshot={"program": program, "schema": schema, "state": after},
                )
            head = store.journal_head()
            self.assertGreater(logical_bytes(store), start.target_bytes)
            controller = driver.service.controller
            with (
                patch.object(driver.service, "cycle", return_value=None),
                patch.object(
                    controller, "retain_history", wraps=controller.retain_history
                ) as retain,
                patch.object(
                    driver.stopping,
                    "wait",
                    side_effect=lambda seconds: driver.stopping.set(),
                ),
            ):
                driver.run()
            retain.assert_called_once_with(
                target_bytes=start.target_bytes, minimum_steps=start.minimum_steps
            )
            self.assertIsNone(driver.hold)
            self.assertEqual(driver.last_retained_turns, 8)
            self.assertEqual(
                [
                    record["data"]["input"]["id"]
                    for record in store.records()
                    if record["kind"] == "turn"
                ],
                ["budget-6", "budget-7", "budget-8"],
            )
            self.assertEqual(
                require_value(store.anchor(), "Expected history pressure to prune")[
                    "input_floor"
                ]["at"],
                5,
            )
            self.assertEqual(store.get("state"), {"processed": 8, "mode": "ascii"})
            self.assertEqual(store.journal_head(), head)
            self.assertLess(logical_bytes(store), start.target_bytes)
