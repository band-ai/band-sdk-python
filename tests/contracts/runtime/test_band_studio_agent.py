from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import pytest

from remote_agent.band.authoring import Journal, canonical, process_one
from remote_agent.band.studio import StudioAgent
from tests.contracts.runtime.required import require_value


@pytest.mark.contract_linux
class TestStudioAgent(unittest.TestCase):
    def test_rejected_retry_is_durably_held_until_exact_ack_then_the_next_job_runs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = Journal(root)
            # Upgrade a pre-fix author journal rather than only a fresh schema.
            journal.db.execute(
                "CREATE TABLE studio_jobs(job TEXT, attempt INTEGER, local_id TEXT NOT NULL, body TEXT, acknowledged INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(job,attempt))"
            )
            journal.db.commit()
            history = [
                {"id": str(n), "role": "human", "text": "x" * 950} for n in range(256)
            ]
            initial = {"operation": "generate", "history": history, "source": None}
            self.assertLessEqual(len(canonical(initial).encode()), 262144)
            retry = dict(
                initial,
                source="invalid source\n" * 2000,
                compiler_feedback={"code": "syntax"},
            )
            self.assertGreater(len(canonical(retry).encode()), 262144)
            rejected = {"id": "retry-job", "attempt": 1, "request": retry}
            next_job = {"id": "next-job", "attempt": 0, "request": initial}
            claims, sent = [], []

            def transport(operation: Any, body: Any) -> dict[str, Any]:
                if operation == "claim":
                    claims.append(body["id"])
                    return {"job": rejected if len(claims) == 1 else next_job}
                self.assertEqual(operation, "retry-job/result")
                sent.append(dict(body))
                if len(sent) == 1:
                    raise TimeoutError("authority accepted; acknowledgement lost")
                return {
                    "id": "retry-job",
                    "attempt": 0 if len(sent) == 2 else 1,
                    "status": "held",
                }

            StudioAgent(journal, transport).poll()
            original_claim = journal.db.execute(
                "SELECT id FROM studio_claim"
            ).fetchone()[0]
            self.assertIsNotNone(original_claim)
            journal.close()
            journal = Journal(root)
            self.addCleanup(journal.close)
            with self.assertRaises(TimeoutError):
                StudioAgent(journal, transport).poll()
            with self.assertRaisesRegex(ValueError, "acknowledgement"):
                StudioAgent(journal, transport).poll()
            self.assertEqual(
                journal.db.execute("SELECT id FROM studio_claim").fetchone()[0],
                original_claim,
            )
            self.assertEqual(claims, [original_claim])
            StudioAgent(journal, transport).poll()
            self.assertEqual(sent, [sent[0]] * 3)
            self.assertEqual(sent[0]["attempt"], 1)
            self.assertTrue(sent[0]["held"])
            self.assertTrue(sent[0]["failure"])
            self.assertNotEqual(claims[1], original_claim)
            self.assertEqual(journal.result("studio:next-job:0")["status"], "pending")
            self.assertEqual(
                journal.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1
            )

    def test_selected_room_job_and_exact_completed_result_survive_an_unknown_ack(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory))
            self.addCleanup(journal.close)
            job = {
                "id": "room-job",
                "attempt": 0,
                "request": {
                    "operation": "generate",
                    "history": [{"id": "h1", "role": "human", "text": "Review"}],
                    "goal": "Formalize",
                },
            }
            sent = []
            queue = [job]

            def transport(operation: Any, body: Any) -> dict[str, Any]:
                if operation == "claim":
                    return {"job": queue.pop(0) if queue else None}
                sent.append(json.dumps(body, sort_keys=True))
                if len(sent) == 1:
                    raise TimeoutError("acceptance response lost")
                return {"id": "room-job", "attempt": 0, "status": "ready"}

            agent = StudioAgent(journal, transport)
            agent.poll()
            calls = []

            def model(value: Any) -> dict[str, Any]:
                calls.append(value)
                return {
                    "source": "compiled by the authority",
                    "derivation": [{"evidence": "h1", "rule": "Review"}],
                    "unresolved": [],
                }

            process_one(journal, model)
            with self.assertRaises(TimeoutError):
                agent.poll()
            restarted = StudioAgent(journal, transport)
            restarted.poll()
            self.assertEqual(len(calls), 1)
            self.assertEqual(sent[0], sent[1])
            self.assertEqual(calls[0], job["request"])

    def test_compiler_retry_ack_can_advance_to_the_next_authority_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory))
            self.addCleanup(journal.close)
            job = {
                "id": "room-job",
                "attempt": 0,
                "request": {"operation": "generate", "history": []},
            }
            queue = [job]

            def transport(operation: Any, body: Any) -> dict[str, Any]:
                if operation == "claim":
                    return {"job": queue.pop(0) if queue else None}
                return {"id": "room-job", "attempt": 1, "status": "queued"}

            agent = StudioAgent(journal, transport)
            agent.poll()
            process_one(
                journal,
                lambda _: {
                    "source": "invalid source",
                    "derivation": [],
                    "unresolved": [],
                },
            )
            agent.poll()
            self.assertEqual(
                journal.db.execute("SELECT acknowledged FROM studio_jobs").fetchone()[
                    0
                ],
                1,
            )

    def test_lost_claim_response_reuses_the_same_durable_claim_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory))
            self.addCleanup(journal.close)
            claims = []
            job = {
                "id": "job-1",
                "attempt": 0,
                "request": {"operation": "generate", "history": []},
            }

            def transport(operation: Any, body: Any) -> dict[str, Any]:
                self.assertEqual(operation, "claim")
                claims.append(body["id"])
                if len(claims) == 1:
                    raise TimeoutError("response lost")
                return {"job": job}

            with self.assertRaises(TimeoutError):
                StudioAgent(journal, transport).poll()
            StudioAgent(journal, transport).poll()
            self.assertEqual(claims[0], claims[1])
            self.assertEqual(journal.result("studio:job-1:0")["status"], "pending")

    def test_full_history_over_128_entries_is_accepted_without_truncation_but_stays_bounded(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory))
            self.addCleanup(journal.close)
            history = [
                {"id": str(i), "role": "human", "text": "Review"} for i in range(129)
            ]
            journal.submit("large-room", {"operation": "generate", "history": history})
            _, job = require_value(journal.claim(), "Expected a claimable journal job")
            self.assertEqual(len(job["history"]), 129)
            with self.assertRaises(ValueError):
                journal.submit(
                    "too-large", {"operation": "generate", "history": history * 8}
                )
