from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.error import HTTPError

import pytest

from remote_agent.band.authoring import canonical, process_one, validate_result
from tests.contracts.runtime.bandauthorwirecapacity import (
    JOB,
    AuthorWireCapacityFixture,
    result,
)
from tests.paths import CONTRACT_RUNTIME_TESTS


@pytest.mark.contract_linux
class TestAuthorWireCapacity(AuthorWireCapacityFixture):
    def test_new_production_output_over_wire_capacity_becomes_one_bounded_failure(
        self,
    ) -> None:
        value = result(large=True)
        validate_result(JOB, value)
        self.assertLess(
            len(canonical(value).encode()),
            524288,
            "Actual fixed-command output fits the model bridge",
        )
        self.assertGreater(
            len(json.dumps({"attempt": 0, "result": value}).encode()), 1048576
        )
        self.claim()
        self.assertTrue(process_one(self.journal, self.provider(value)))
        saved = self.journal.result("studio:saved:0")
        self.assertEqual(
            saved["status"],
            "failed",
            "Unsendable new output must not be persisted as complete",
        )
        self.reply = lambda body: (
            200,
            {"id": "saved", "attempt": 0, "status": "failed"},
        )
        self.agent().poll()
        self.assertEqual(self.journal.result("studio:later:0")["status"], "pending")
        self.assertEqual(len(self.requests), 1)
        failure = json.loads(self.requests[0])
        self.assertEqual(set(failure), {"attempt", "failure", "held"})
        self.assertFalse(failure["held"])
        self.assertTrue(1 <= len(failure["failure"].encode()) <= 2000)
        self.assertEqual(
            (self.root / "provider-calls").read_text().splitlines(), ["call"]
        )
        self.journal.close()
        self.journal = self.open_journal()
        self.journal.recover()
        self.assertEqual(self.journal.result("studio:saved:0"), saved)
        self.assertEqual(self.agent().held_completions(), [])
        self.agent().poll()
        self.assertEqual(
            len(self.requests), 1, "Acknowledged failure must not be delivered twice"
        )

    def test_saved_over_capacity_body_is_held_after_restart_without_blocking_later_claims(
        self,
    ) -> None:
        self.claim()
        # A generic/local author remains free of the production transport policy.
        self.assertTrue(process_one(self.journal, lambda job: result(large=True)))
        self.assertEqual(self.journal.result("studio:saved:0")["status"], "complete")
        output = self.journal.result("studio:saved:0")["result"]
        body = json.dumps(
            {"attempt": 0, "result": output}, ensure_ascii=False, indent=1
        )
        with self.journal.db:
            self.journal.db.execute(
                "UPDATE studio_jobs SET body=? WHERE job=?", (body, "saved")
            )
        saved_job = self.journal.db.execute(
            "SELECT * FROM jobs WHERE id=?", ("studio:saved:0",)
        ).fetchone()
        self.journal.close()
        self.journal = self.open_journal()
        self.journal.recover()
        agent = self.agent()
        agent.poll()
        held = [
            {
                "job": "saved",
                "attempt": 0,
                "local_id": "studio:saved:0",
                "status": "request_capacity",
                "acknowledged": False,
            }
        ]
        self.assertEqual(agent.held_completions(), held)
        self.assertEqual(self.events, held)
        self.assertEqual(
            self.requests, [], "A local request refusal never reaches HTTP"
        )
        self.assertEqual(self.journal.result("studio:later:0")["status"], "pending")
        self.journal.close()
        self.journal = self.open_journal()
        self.journal.recover()
        agent = self.agent()
        self.assertEqual(agent.held_completions(), held)
        claim_count = len(self.claims)
        self.assertEqual(agent.retry_held("saved", 0), held[0])
        self.assertEqual(len(self.claims), claim_count)
        self.assertEqual(self.requests, [])
        self.assertEqual(
            self.journal.db.execute(
                "SELECT body,acknowledged FROM studio_jobs WHERE job=?", ("saved",)
            ).fetchone(),
            (body, 0),
        )
        self.assertEqual(
            self.journal.db.execute(
                "SELECT * FROM jobs WHERE id=?", ("studio:saved:0",)
            ).fetchone(),
            saved_job,
        )
        self.assertEqual(
            len(self.events),
            1,
            "An explicit held retry must not emit a duplicate initial-hold event",
        )

    def test_small_production_result_keeps_current_serializer_and_advances_queue(
        self,
    ) -> None:
        self.claim()
        value = result()
        self.assertTrue(process_one(self.journal, self.provider(value)))
        self.assertEqual(self.journal.result("studio:saved:0")["status"], "complete")
        self.agent().poll()
        self.assertEqual(
            self.requests,
            [
                json.dumps(
                    {"attempt": 0, "result": json.loads(canonical(value))}
                ).encode()
            ],
        )
        self.assertEqual(self.journal.result("studio:later:0")["status"], "pending")
        self.assertEqual(self.agent().held_completions(), [])
        self.assertEqual(
            (self.root / "provider-calls").read_text().splitlines(), ["call"]
        )

    def test_compiled_response_over_capacity_is_held_with_unknown_ack_and_exact_retry(
        self,
    ) -> None:
        # This template was produced by the pinned frozen engine, not a guessed
        # definition. Its hashes bind the expanded source and response witness.
        fixture = json.loads(
            (CONTRACT_RUNTIME_TESTS / "author_response_capacity.json").read_text()
        )
        value = result()
        doc = "d" * fixture["doc_bytes"]
        value["source"] = value["source"].replace(
            "You are A. Ping B while the connection is in use, and close it when you are done.",
            doc,
        )
        value["derivation"] = [{"evidence": "m1", "rule": "r" * 4000}] * fixture[
            "derivation_rules"
        ]
        source_hash = hashlib.sha256(value["source"].encode()).hexdigest()
        self.assertEqual(source_hash, fixture["source_sha256"])
        validate_result(JOB, value)

        def expanded(item: Any) -> Any:
            if item == "$role_doc":
                return doc
            if isinstance(item, dict):
                return {k: expanded(v) for k, v in item.items()}
            if isinstance(item, list):
                return [expanded(v) for v in item]
            return item

        response = {
            "id": "saved",
            "attempt": 0,
            "status": "ready",
            "result": value,
            "source": {
                "profile": "band-contract/file-source/1",
                "format": "frontmatter",
                "content": value["source"],
                "sha256": source_hash,
            },
            "definition": expanded(fixture["definition"]),
        }
        encoded = canonical(response).encode()
        self.assertEqual(
            hashlib.sha256(encoded).hexdigest(), fixture["response_sha256"]
        )
        self.assertGreater(len(encoded), 1048576)
        self.assertLess(len(canonical(value).encode()), 524288)
        self.assertLess(
            len(json.dumps({"attempt": 0, "result": value}).encode()), 1048576
        )
        self.claim()
        self.assertTrue(process_one(self.journal, self.provider(value)))
        self.assertEqual(self.journal.result("studio:saved:0")["status"], "complete")
        original_job = self.journal.db.execute(
            "SELECT * FROM jobs WHERE id=?", ("studio:saved:0",)
        ).fetchone()
        self.reply = lambda body: (200, encoded)
        agent = self.agent()
        agent.poll()
        held = [
            {
                "job": "saved",
                "attempt": 0,
                "local_id": "studio:saved:0",
                "status": "response_capacity",
                "acknowledged": False,
            }
        ]
        self.assertEqual(agent.held_completions(), held)
        self.assertEqual(self.events, held)
        self.assertEqual(self.journal.result("studio:later:0")["status"], "pending")
        self.assertEqual(
            len(self.requests),
            1,
            "The request reached HTTP; acknowledgement stays unknown",
        )
        saved_body = self.journal.db.execute(
            "SELECT body FROM studio_jobs WHERE job=?", ("saved",)
        ).fetchone()[0]
        self.journal.close()
        self.journal = self.open_journal()
        self.journal.recover()
        agent = self.agent()
        self.assertEqual(agent.held_completions(), held)
        agent.poll()
        self.assertEqual(
            len(self.requests), 1, "No automatic replay of a held completion"
        )
        claim_count = len(self.claims)
        self.assertEqual(agent.retry_held("saved", 0), held[0])
        self.assertEqual(agent.held_completions(), held)
        self.reply = lambda body: (
            200,
            {"id": "saved", "attempt": 0, "status": "ready"},
        )
        self.assertEqual(
            agent.retry_held("saved", 0),
            dict(held[0], status="acknowledged", acknowledged=True),
        )
        self.assertEqual(agent.held_completions(), [])
        self.assertEqual(self.requests, [self.requests[0]] * 3)
        self.assertEqual(
            len(self.claims), claim_count, "Explicit retry never claims work"
        )
        self.assertEqual(
            self.journal.db.execute(
                "SELECT body,acknowledged FROM studio_jobs WHERE job=?", ("saved",)
            ).fetchone(),
            (saved_body, 1),
        )
        self.assertEqual(
            self.journal.db.execute(
                "SELECT * FROM jobs WHERE id=?", ("studio:saved:0",)
            ).fetchone(),
            original_job,
        )
        self.assertEqual(
            (self.root / "provider-calls").read_text().splitlines(), ["call"]
        )
        self.assertEqual(self.events, held)

    def test_unrelated_http_and_malformed_acknowledgements_remain_ambiguous(
        self,
    ) -> None:
        self.claim()
        process_one(self.journal, lambda job: result())
        agent = self.agent()
        cases = [
            (503, {"error": "unavailable"}),
            (422, {"error": "invalid_result"}),
            (404, {"error": "proxy_not_found"}),
            (200, {"unexpected": True}),
            (200, b"{"),
        ]
        for status, body in cases:
            with self.subTest(status=status, body=body):
                self.reply = lambda sent, status=status, body=body: (status, body)
                with self.assertRaises((HTTPError, ValueError)) as raised:
                    agent.poll()
                if isinstance(raised.exception, HTTPError):
                    raised.exception.close()
                self.assertEqual(agent.held_completions(), [])
                self.assertEqual(len(self.claims), 1)
                self.assertEqual(
                    self.journal.db.execute(
                        "SELECT acknowledged FROM studio_jobs"
                    ).fetchone()[0],
                    0,
                )
        self.assertEqual(self.requests, [self.requests[0]] * len(cases))
