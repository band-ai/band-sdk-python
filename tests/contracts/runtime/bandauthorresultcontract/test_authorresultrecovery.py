from __future__ import annotations

import json
from typing import Any, Never
from urllib.error import HTTPError

import pytest

from remote_agent.band.authoring import Journal, canonical, process_one
from remote_agent.band.studio import StudioAgent
from tests.contracts.runtime.bandauthorresultcontract.kit import (
    JOB,
    AuthorResultRecoveryFixture,
    invalid_outputs,
    output,
    response_error,
)


@pytest.mark.contract_linux
class TestAuthorResultRecovery(AuthorResultRecoveryFixture):
    def test_invalid_new_outputs_complete_as_failure_once_without_resampling(
        self,
    ) -> None:
        for name, invalid in invalid_outputs().items():
            with self.subTest(name=name):
                root, journal = self.fixture()
                queue = [
                    {"id": "invalid", "attempt": 0, "request": JOB},
                    {"id": "live", "attempt": 0, "request": JOB},
                ]
                sent, calls = [], []

                def transport(
                    operation: Any,
                    body: Any,
                    *,
                    queue: list[dict[str, Any]] = queue,
                    sent: list[tuple[str, dict[str, Any]]] = sent,
                ) -> dict[str, Any]:
                    if operation == "claim":
                        return {"job": queue.pop(0) if queue else None}
                    sent.append((operation, body))
                    if operation == "invalid/result":
                        self.assertEqual(set(body), {"attempt", "failure", "held"})
                        self.assertFalse(body["held"])
                        self.assertTrue(
                            1 <= len(body["failure"].encode("utf-8")) <= 2000
                        )
                    return {
                        "id": operation.removesuffix("/result"),
                        "attempt": 0,
                        "status": "failed"
                        if operation == "invalid/result"
                        else "ready",
                    }

                agent = StudioAgent(journal, transport)
                agent.poll()

                def model(
                    request: Any,
                    *,
                    calls: list[dict[str, Any]] = calls,
                    invalid: Any = invalid,
                ) -> Any:
                    calls.append(request)
                    return invalid

                self.assertTrue(process_one(journal, model))
                self.assertEqual(journal.result("studio:invalid:0")["status"], "failed")
                failed = journal.result("studio:invalid:0")
                agent.poll()
                self.assertEqual(journal.result("studio:live:0")["status"], "pending")
                journal.close()
                journal = Journal(root)
                self.addCleanup(journal.close)
                journal.recover()
                self.assertTrue(process_one(journal, lambda _: output()))
                StudioAgent(journal, transport).poll()
                journal.submit("studio:invalid:0", JOB)
                self.assertFalse(
                    process_one(
                        journal,
                        lambda _: self.fail("Invalid output cannot be resampled"),
                    )
                )
                self.assertEqual(journal.result("studio:invalid:0"), failed)
                self.assertEqual(len(calls), 1)
                self.assertEqual(
                    len([op for op, _ in sent if op == "invalid/result"]), 1
                )

    def test_failure_completion_uses_a_nonempty_utf8_byte_bound(self) -> None:
        for detail in ("", "😀" * 1800, "é" * 1800, "failure"):
            with self.subTest(detail_size=len(detail)):
                _, journal = self.fixture()
                claimed, sent = [], []

                def transport(
                    operation: Any,
                    body: Any,
                    *,
                    claimed: list[dict[str, Any]] = claimed,
                    sent: list[dict[str, Any]] = sent,
                ) -> dict[str, Any]:
                    if operation == "claim":
                        claimed.append(body)
                        return {
                            "job": {"id": "failure", "attempt": 0, "request": JOB}
                            if len(claimed) == 1
                            else None
                        }
                    sent.append(body)
                    self.assertTrue(1 <= len(body["failure"].encode("utf-8")) <= 2000)
                    self.assertFalse(body["held"])
                    return {"id": "failure", "attempt": 0, "status": "failed"}

                agent = StudioAgent(journal, transport)
                agent.poll()

                def fail(_: Any, *, detail: str = detail) -> Never:
                    raise ValueError(detail)

                process_one(journal, fail)
                agent.poll()
                self.assertEqual(
                    journal.db.execute(
                        "SELECT acknowledged FROM studio_jobs"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(len(sent), 1)

    def test_legacy_invalid_result_is_held_without_changing_output_body_or_attempt(
        self,
    ) -> None:
        values = {
            key: invalid_outputs()[key]
            for key in (
                "unresolved_count",
                "derivation_count",
                "empty_choice",
                "rule_utf8",
            )
        }
        for name, invalid in values.items():
            for staged in (False, True):
                with self.subTest(name=name, staged=staged):
                    root, journal = self.fixture()
                    # Old schema and old complete output, predating validation.
                    journal.db.execute(
                        "CREATE TABLE studio_jobs(job TEXT, attempt INTEGER, local_id TEXT NOT NULL, body TEXT, acknowledged INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(job,attempt))"
                    )
                    journal.submit("studio:legacy:1", JOB)
                    journal.claim()
                    journal.finish("studio:legacy:1", "complete", invalid)
                    envelope = {"attempt": 1, "result": invalid}
                    original_body = (
                        json.dumps(envelope, ensure_ascii=True, indent=1)
                        if staged
                        else None
                    )
                    with journal.db:
                        journal.db.execute(
                            "INSERT INTO studio_jobs VALUES(?,?,?,?,0)",
                            ("legacy", 1, "studio:legacy:1", original_body),
                        )
                    original_job = journal.db.execute(
                        "SELECT * FROM jobs WHERE id='studio:legacy:1'"
                    ).fetchone()
                    journal.close()
                    journal = Journal(root)
                    self.addCleanup(journal.close)
                    sent, claims, events = [], [], []

                    def transport(
                        operation: Any,
                        body: Any,
                        *,
                        claims: list[str] = claims,
                        sent: list[dict[str, Any]] = sent,
                    ) -> dict[str, Any]:
                        if operation == "claim":
                            claims.append(body["id"])
                            return {
                                "job": {"id": "live", "attempt": 0, "request": JOB}
                                if len(claims) == 1
                                else None
                            }
                        sent.append(body)
                        raise response_error()

                    agent = StudioAgent(journal, transport, on_hold=events.append)
                    agent.poll()
                    saved_body, acknowledged, hold = journal.db.execute(
                        "SELECT body,acknowledged,completion_hold FROM studio_jobs WHERE job='legacy'"
                    ).fetchone()
                    self.assertEqual(
                        saved_body, original_body if staged else canonical(envelope)
                    )
                    self.assertEqual((acknowledged, hold), (0, "invalid_result"))
                    self.assertEqual(sent, [])
                    self.assertEqual(
                        journal.result("studio:live:0")["status"], "pending"
                    )
                    self.assertEqual(
                        journal.db.execute(
                            "SELECT * FROM jobs WHERE id='studio:legacy:1'"
                        ).fetchone(),
                        original_job,
                    )
                    self.assertEqual(events, agent.held_completions())
                    self.assertEqual(len(events), 1)
                    journal.close()
                    journal = Journal(root)
                    self.addCleanup(journal.close)
                    journal.recover()
                    agent = StudioAgent(journal, transport)
                    held = agent.held_completions()
                    before_claims = list(claims)
                    with self.assertRaises(HTTPError) as raised:
                        agent.retry_held("legacy", 1)
                    raised.exception.close()
                    self.assertEqual(sent, [envelope])
                    self.assertEqual(claims, before_claims)
                    self.assertEqual(agent.held_completions(), held)
                    self.assertEqual(
                        journal.db.execute(
                            "SELECT body,acknowledged,attempt FROM studio_jobs WHERE job='legacy'"
                        ).fetchone(),
                        (saved_body, 0, 1),
                    )
                    self.assertEqual(
                        journal.db.execute(
                            "SELECT * FROM jobs WHERE id='studio:legacy:1'"
                        ).fetchone(),
                        original_job,
                    )
                    accepted = []
                    agent.transport = lambda op, body, *, accepted=accepted: (
                        accepted.append((op, body))
                        or {"id": "legacy", "attempt": 1, "status": "ready"}
                    )
                    self.assertTrue(agent.retry_held("legacy", 1)["acknowledged"])
                    self.assertEqual(accepted, [("legacy/result", envelope)])
                    self.assertEqual(agent.held_completions(), [])
                    self.assertEqual(
                        journal.db.execute(
                            "SELECT body,acknowledged FROM studio_jobs WHERE job='legacy'"
                        ).fetchone(),
                        (saved_body, 1),
                    )
                    model_calls = []
                    self.assertTrue(
                        process_one(
                            journal,
                            lambda job, *, model_calls=model_calls: (
                                model_calls.append(job) or output()
                            ),
                        )
                    )
                    self.assertFalse(
                        process_one(
                            journal,
                            lambda _: self.fail("Legacy output cannot be resampled"),
                        )
                    )
                    self.assertEqual(model_calls, [JOB])
                    self.assertEqual(
                        journal.result("studio:live:0")["status"], "complete"
                    )
                    self.assertEqual(
                        journal.db.execute(
                            "SELECT * FROM jobs WHERE id='studio:legacy:1'"
                        ).fetchone(),
                        original_job,
                    )

    def test_local_hold_releases_only_its_matching_rejected_claim(self) -> None:
        for pending_claim in ("rejected-claim", "unrelated-claim"):
            with self.subTest(pending_claim=pending_claim):
                _, journal = self.fixture()
                claims = []
                agent = StudioAgent(
                    journal,
                    lambda op, body, *, claims=claims: (
                        claims.append(body["id"]) or {"job": None}
                    ),
                )
                body = canonical({"attempt": 0, "failure": "", "held": True})
                with journal.db:
                    journal.db.execute(
                        "INSERT INTO studio_jobs VALUES(?,?,?,?,0,?,NULL)",
                        ("legacy", 0, "studio:legacy:0", body, "rejected-claim"),
                    )
                    journal.db.execute("UPDATE studio_claim SET id=?", (pending_claim,))
                agent.poll()
                self.assertEqual(
                    claims[0] == pending_claim, pending_claim == "unrelated-claim"
                )
                self.assertEqual(
                    journal.db.execute(
                        "SELECT body,rejected_claim,acknowledged FROM studio_jobs"
                    ).fetchone(),
                    (body, "rejected-claim", 0),
                )

    def test_legacy_invalid_failure_body_is_retained_and_cannot_pin_other_claims(
        self,
    ) -> None:
        for detail in ("", "😀" * 1800):
            with self.subTest(detail_size=len(detail)):
                _, journal = self.fixture()
                calls = []
                agent = StudioAgent(
                    journal,
                    lambda op, body, *, calls=calls: calls.append(op) or {"job": None},
                )
                body = canonical({"attempt": 0, "failure": detail, "held": False})
                with journal.db:
                    journal.db.execute(
                        "INSERT INTO studio_jobs VALUES(?,?,?,?,0,NULL,NULL)",
                        ("legacy", 0, "studio:legacy:0", body),
                    )
                agent.poll()
                self.assertEqual(calls, ["claim"])
                self.assertEqual(
                    journal.db.execute(
                        "SELECT body,acknowledged,completion_hold FROM studio_jobs"
                    ).fetchone(),
                    (body, 0, "invalid_completion"),
                )

    def test_transport_status_alone_never_classifies_a_valid_completion_as_invalid(
        self,
    ) -> None:
        for status, body in (
            (422, b'{"error":"invalid_result"}'),
            (422, b'{"error":"invalid_request"}'),
            (409, b'{"error":"id_conflict"}'),
            (503, b'{"error":"unavailable"}'),
        ):
            with self.subTest(status=status, body=body):
                _, journal = self.fixture()
                claims, sent = [], []

                def transport(
                    operation: Any,
                    value: Any,
                    *,
                    body: bytes = body,
                    claims: list[dict[str, Any]] = claims,
                    sent: list[dict[str, Any]] = sent,
                    status: int = status,
                ) -> dict[str, Any]:
                    if operation == "claim":
                        claims.append(value)
                        return {"job": {"id": "valid", "attempt": 0, "request": JOB}}
                    sent.append(value)
                    raise response_error(status, body)

                agent = StudioAgent(journal, transport)
                agent.poll()
                process_one(journal, lambda _: output())
                for _ in range(2):
                    with self.assertRaises(HTTPError) as raised:
                        agent.poll()
                    raised.exception.close()
                self.assertEqual(len(claims), 1)
                self.assertEqual(sent, [sent[0], sent[0]])
                self.assertEqual(agent.held_completions(), [])
                self.assertEqual(
                    journal.db.execute(
                        "SELECT acknowledged FROM studio_jobs"
                    ).fetchone()[0],
                    0,
                )
