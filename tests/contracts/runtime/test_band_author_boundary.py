from __future__ import annotations

import json
from typing import Any

import pytest

from remote_agent.band.authoring import Journal, process_one
from remote_agent.band.studio import StudioAgent
from tests.contracts.runtime.bandauthorboundary import AuthorBoundaryFixture


@pytest.mark.contract_linux
class TestAuthorBoundary(AuthorBoundaryFixture):
    def test_nonobject_local_job_is_a_validation_error_without_a_journal_row(
        self,
    ) -> None:
        for job in (None, [], 1, "not an object"):
            with self.subTest(job=job):
                _, journal = self.fixture()
                with self.assertRaises(ValueError):
                    journal.submit("invalid", job)
                self.assertEqual(
                    journal.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0
                )

    def test_malformed_history_stages_the_exact_rejection_across_restart(self) -> None:
        for entry in (
            1,
            ["id", "role", "text"],
            None,
            {"id": "m1", "role": "human", "text": None},
        ):
            with self.subTest(entry=entry):
                root, journal = self.fixture()
                job = {
                    "id": "malformed",
                    "attempt": 0,
                    "request": {"operation": "generate", "history": [entry]},
                }
                claims, sent = [], []

                def transport(
                    operation: Any,
                    body: Any,
                    *,
                    claims: list[str] = claims,
                    job: dict[str, Any] = job,
                    sent: list[dict[str, Any]] = sent,
                ) -> dict[str, Any]:
                    if operation == "claim":
                        claims.append(body["id"])
                        return {"job": job if len(claims) == 1 else None}
                    self.assertEqual(operation, "malformed/result")
                    sent.append(body)
                    return {"id": "malformed", "attempt": 0, "status": "held"}

                StudioAgent(journal, transport).poll()
                original_claim = journal.db.execute(
                    "SELECT id FROM studio_claim"
                ).fetchone()[0]
                original_body = journal.db.execute(
                    "SELECT body FROM studio_jobs"
                ).fetchone()[0]
                self.assertTrue(json.loads(original_body)["held"])
                self.assertEqual(
                    journal.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0
                )
                journal.close()
                journal = Journal(root)
                self.addCleanup(journal.close)
                self.assertFalse(
                    process_one(
                        journal,
                        lambda _: self.fail("Rejected input cannot invoke a model"),
                    )
                )
                StudioAgent(journal, transport).poll()
                self.assertEqual(sent, [json.loads(original_body)])
                self.assertNotEqual(claims[-1], original_claim)
                self.assertEqual(
                    journal.db.execute(
                        "SELECT acknowledged FROM studio_jobs"
                    ).fetchone()[0],
                    1,
                )

    def test_nonobject_claim_response_preserves_the_claim_for_retry(self) -> None:
        for response in (None, [], 1, "not an object"):
            with self.subTest(response=response):
                root, journal = self.fixture()
                claims = []

                def transport(
                    operation: Any,
                    body: Any,
                    *,
                    claims: list[str] = claims,
                    response: object = response,
                ) -> Any:
                    self.assertEqual(operation, "claim")
                    claims.append(body["id"])
                    return response if len(claims) == 1 else {"job": None}

                with self.assertRaises(ValueError):
                    StudioAgent(journal, transport).poll()
                journal.close()
                journal = Journal(root)
                self.addCleanup(journal.close)
                StudioAgent(journal, transport).poll()
                self.assertEqual(claims, [claims[0], claims[0]])
                self.assertIsNone(
                    journal.db.execute("SELECT id FROM studio_claim").fetchone()[0]
                )
                self.assertEqual(
                    journal.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0
                )

    def test_nonobject_ack_keeps_completed_model_output_and_exact_result_body(
        self,
    ) -> None:
        for response in (None, [], 1, "not an object"):
            with self.subTest(response=response):
                root, journal = self.fixture()
                job = {
                    "id": "valid",
                    "attempt": 0,
                    "request": {"operation": "generate", "history": []},
                }
                claims, results, calls = [], [], []

                def transport(
                    operation: Any,
                    body: Any,
                    *,
                    claims: list[str] = claims,
                    job: dict[str, Any] = job,
                    response: object = response,
                    results: list[dict[str, Any]] = results,
                ) -> Any:
                    if operation == "claim":
                        claims.append(body["id"])
                        return {"job": job if len(claims) == 1 else None}
                    self.assertEqual(operation, "valid/result")
                    results.append(body)
                    return (
                        response
                        if len(results) == 1
                        else {"id": "valid", "attempt": 0, "status": "ready"}
                    )

                studio = StudioAgent(journal, transport)
                studio.poll()

                def model(
                    value: Any, *, calls: list[dict[str, Any]] = calls
                ) -> dict[str, Any]:
                    calls.append(value)
                    return {
                        "source": "typed fixture",
                        "derivation": [],
                        "unresolved": [],
                    }

                self.assertTrue(process_one(journal, model))
                with self.assertRaises(ValueError):
                    studio.poll()
                journal.close()
                journal = Journal(root)
                self.addCleanup(journal.close)
                StudioAgent(journal, transport).poll()
                self.assertEqual(results, [results[0], results[0]])
                self.assertEqual(len(calls), 1)
                self.assertEqual(journal.result("studio:valid:0")["status"], "complete")
                self.assertFalse(
                    process_one(
                        journal,
                        lambda _: self.fail("Completed output cannot be resampled"),
                    )
                )
