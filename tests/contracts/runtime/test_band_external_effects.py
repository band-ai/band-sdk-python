from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import pytest

from remote_agent.band.effects import EffectRunner
from remote_agent.store import Store


@pytest.mark.contract_linux
class TestExternalEffect(unittest.TestCase):
    def test_unknown_ack_retains_the_exact_request_and_a_completed_retry_has_no_second_effect(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            sent = []
            body = {"id": "proposal", "kind": "question", "payload": {"a": 37, "b": 61}}

            def send(request: Any) -> dict[str, Any]:
                sent.append(request)
                if len(sent) == 1:
                    raise TimeoutError("ack lost")
                return {"id": "proposal", "status": "duplicate"}

            with Store(path) as store:
                with self.assertRaises(TimeoutError):
                    EffectRunner(store).run(
                        "task", body, send, lambda r, q: r["id"] == q["id"]
                    )
                self.assertEqual(store.get("external_pending"), ["task"])
            with Store(path) as store:
                runner = EffectRunner(store)
                result = runner.run("task", body, send, lambda r, q: r["id"] == q["id"])
                self.assertEqual(
                    runner.run("task", body, send, lambda r, q: True), result
                )
                self.assertEqual(sent, [body, body])
                self.assertEqual(store.get("external_pending"), [])
                self.assertEqual(
                    [r["kind"] for r in store.records()],
                    ["external_input", "external_result"],
                )
                with self.assertRaisesRegex(ValueError, "identity_conflict"):
                    runner.run(
                        "task", dict(body, kind="different"), send, lambda r, q: True
                    )
