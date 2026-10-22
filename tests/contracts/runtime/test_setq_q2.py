from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from remote_agent.storage import StoreError, canonical
from tests.contracts.runtime.setqq2 import StageCapacityFixture


@pytest.mark.contract_linux
class TestStageCapacity(StageCapacityFixture):
    def test_default_one_mib_cap_does_not_duplicate_staged_payload(self) -> None:
        self.start(before={"padding": "b" * 230_000})
        result = self.receive(
            {
                "type": "handled",
                "state": {"padding": "n" * 185_000},
                "message": {"kind": "answer", "payload": {"text": "m" * 60_000}},
            }
        )
        self.assert_completed(result, "program")
        self.provider.respond.assert_not_called()
        stage = next(row for row in self.store.records() if row["kind"] == "stage")
        staged = stage["updates"]["staged"]
        self.assertGreater(len(canonical(staged)) * 2, 1_048_576)
        self.assertLessEqual(
            len(canonical(stage["data"])) + len(canonical(stage["updates"])), 1_048_576
        )
        self.assertEqual(
            stage["data"], {"input": "q1", "invocation": staged["invocation"]}
        )

    def test_stage_capacity_error_faults_and_falls_back_without_replaying_program(
        self,
    ) -> None:
        self.start()
        original = self.store.commit
        rejected = []

        def commit(kind: Any, data: Any, **kwargs: Any) -> Any:
            if kind == "stage" and kwargs["updates"]["staged"]["producer"] == "program":
                rejected.append(kind)
                raise StoreError("Journal record capacity exceeded")
            return original(kind, data, **kwargs)

        with patch.object(self.store, "commit", side_effect=commit):
            result = self.receive(
                {
                    "type": "handled",
                    "state": {"count": 1},
                    "message": {"kind": "answer", "payload": {"text": "program"}},
                }
            )
        self.assert_completed(result, "llm")
        self.assertEqual(rejected, ["stage"])
        self.provider.respond.assert_called_once()
        self.assertIn("program_failure", self.store.get("fault")["reason"])
        self.assertEqual(
            len(
                [row for row in self.store.records() if row["kind"] == "program_output"]
            ),
            1,
        )
        self.assertEqual(self.store.get("attempts:q1"), 2)

    def test_valid_result_store_error_is_a_program_fault(self) -> None:
        self.start()
        original = self.agent._valid_result

        def validate(result: Any) -> Any:
            if result["state"].get("invalid"):
                raise StoreError("Invalid JSON encoding or nesting")
            return original(result)

        with patch.object(self.agent, "_valid_result", side_effect=validate):
            result = self.receive(
                {"type": "handled", "state": {"invalid": True}, "message": None}
            )
        self.assert_completed(result, "llm")
        self.assertIn("program_failure", self.store.get("fault")["reason"])
        self.provider.respond.assert_called_once()

    def test_store_error_after_stage_keeps_outbox_for_reconciliation(self) -> None:
        self.start()
        original = self.store.commit

        def commit(kind: Any, *args: Any, **kwargs: Any) -> Any:
            if kind == "turn":
                raise StoreError("Simulated finalization failure")
            return original(kind, *args, **kwargs)

        with patch.object(self.store, "commit", side_effect=commit):
            result = self.receive(
                {
                    "type": "handled",
                    "state": {"count": 1},
                    "message": {"kind": "answer", "payload": {"text": "program"}},
                }
            )
        self.assertEqual(result["status"], "held")
        self.assertIsNotNone(self.store.get("staged"))
        self.assertIsNone(self.store.get("execution"))
        self.provider.respond.assert_not_called()
        self.assertEqual(self.store.get("mode"), "program")
