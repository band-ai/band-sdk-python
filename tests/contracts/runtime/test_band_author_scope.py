from __future__ import annotations

import io
import json
import sys
import threading
from contextlib import redirect_stdout
from typing import Any, Never
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from remote_agent.band import author_service
from remote_agent.band.authoring import Journal, canonical, process_one
from remote_agent.band.studio import StudioAgent
from tests.contracts.runtime.bandauthorscope import AuthorScopeFixture, http_error

if sys.platform == "linux":
    import fcntl


@pytest.mark.contract_linux
class TestAuthorScope(AuthorScopeFixture):
    def test_scope_loss_after_claim_preserves_unknown_completion_and_allows_later_work(
        self,
    ) -> None:
        for lifecycle in (
            "room_deleted",
            "author_removed",
            "author_removed_after_lost_ack",
        ):
            with self.subTest(lifecycle=lifecycle):
                root, journal = self.fixture(legacy=True)
                queue = [self.job("unavailable"), self.job("live"), self.job("later")]
                claims, sent, calls, events = [], [], [], []
                scope_available = threading.Event()

                def transport(
                    operation: Any,
                    body: Any,
                    *,
                    claims: list[str] = claims,
                    lifecycle: str = lifecycle,
                    queue: list[dict[str, Any]] = queue,
                    scope_available: threading.Event = scope_available,
                    sent: list[tuple[str, str]] = sent,
                ) -> dict[str, Any]:
                    if operation == "claim":
                        claims.append(body["id"])
                        return {"job": queue.pop(0) if queue else None}
                    sent.append((operation, canonical(body)))
                    if (
                        operation == "unavailable/result"
                        and not scope_available.is_set()
                    ):
                        if lifecycle.endswith("lost_ack") and len(sent) == 1:
                            raise TimeoutError(
                                "completion committed; acknowledgement lost before membership removal"
                            )
                        raise http_error()
                    return {
                        "id": operation.removesuffix("/result"),
                        "attempt": 0,
                        "status": "ready",
                    }

                agent = StudioAgent(journal, transport)
                agent.poll()

                def model(request: Any, *, calls: list[dict[str, Any]] = calls) -> Any:
                    calls.append(request)
                    return self.output()

                self.assertTrue(process_one(journal, model))
                if lifecycle.endswith("lost_ack"):
                    with self.assertRaises(TimeoutError):
                        agent.poll()
                # This call used to raise the scope error before claiming the live job.
                agent.poll()
                self.assertEqual(journal.result("studio:live:0")["status"], "pending")
                original_body, acknowledged, hold = journal.db.execute(
                    "SELECT body,acknowledged,completion_hold FROM studio_jobs WHERE job='unavailable'"
                ).fetchone()
                self.assertEqual((acknowledged, hold), (0, "scope_unavailable"))
                self.assertEqual(
                    json.loads(original_body), {"attempt": 0, "result": self.output()}
                )
                original_result = journal.result("studio:unavailable:0")
                self.assertEqual(original_result["status"], "complete")
                self.assertEqual(len(calls), 1)
                journal.close()
                journal = Journal(root)
                self.addCleanup(journal.close)
                journal.recover()
                agent = StudioAgent(journal, transport, on_hold=events.append)
                held = agent.held_completions()
                self.assertEqual(
                    held,
                    [
                        {
                            "job": "unavailable",
                            "attempt": 0,
                            "local_id": "studio:unavailable:0",
                            "status": "scope_unavailable",
                            "acknowledged": False,
                        }
                    ],
                )
                self.assertEqual(
                    journal.result("studio:unavailable:0"), original_result
                )
                self.assertTrue(process_one(journal, model))
                agent.poll()
                self.assertEqual(journal.result("studio:later:0")["status"], "pending")
                self.assertEqual(agent.held_completions(), held)
                self.assertEqual(len(calls), 2)
                self.assertEqual(events, [])
                unavailable_sends = [
                    body
                    for operation, body in sent
                    if operation == "unavailable/result"
                ]
                self.assertEqual(
                    unavailable_sends,
                    [original_body] * (2 if lifecycle.endswith("lost_ack") else 1),
                )
                scope_available.set()
                claims_before_retry = list(claims)
                outcome = agent.retry_held("unavailable", 0)
                self.assertTrue(outcome["acknowledged"])
                self.assertEqual(agent.held_completions(), [])
                self.assertEqual(claims, claims_before_retry)
                self.assertEqual(sent[-1], ("unavailable/result", original_body))
                self.assertEqual(
                    journal.result("studio:unavailable:0"), original_result
                )
                self.assertEqual(len(calls), 2)
                self.assertEqual(
                    journal.db.execute(
                        "SELECT body,acknowledged,completion_hold FROM studio_jobs WHERE job='unavailable'"
                    ).fetchone(),
                    (original_body, 1, None),
                )

    def test_rejected_input_hold_releases_only_its_claim_and_retains_rejection_evidence(
        self,
    ) -> None:
        _, journal = self.fixture()
        rejected = self.job("rejected")
        rejected["request"]["history"] = [None]
        queue = [rejected, self.job("live")]
        claims, sent, events = [], [], []

        def transport(operation: Any, body: Any) -> dict[str, Any]:
            if operation == "claim":
                claims.append(body["id"])
                return {"job": queue.pop(0) if queue else None}
            sent.append(canonical(body))
            raise http_error(content_type="application/json; charset=utf-8")

        agent = StudioAgent(journal, transport, on_hold=events.append)
        agent.poll()
        original_claim = journal.db.execute("SELECT id FROM studio_claim").fetchone()[0]
        agent.poll()
        self.assertNotEqual(claims[-1], original_claim)
        self.assertEqual(journal.result("studio:live:0")["status"], "pending")
        self.assertEqual(
            journal.db.execute(
                "SELECT rejected_claim,acknowledged,completion_hold FROM studio_jobs WHERE job='rejected'"
            ).fetchone(),
            (original_claim, 0, "scope_unavailable"),
        )
        self.assertEqual(
            sent,
            [
                journal.db.execute(
                    "SELECT body FROM studio_jobs WHERE job='rejected'"
                ).fetchone()[0]
            ],
        )
        self.assertEqual(events, agent.held_completions())
        self.assertEqual(len(events), 1)
        self.assertEqual(
            journal.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1
        )
        with self.assertRaises(KeyError):
            journal.result("studio:rejected:0")

    def test_only_the_exact_application_scope_error_creates_a_hold(self) -> None:
        errors = [
            lambda: TimeoutError("unknown outcome"),
            lambda: http_error(503, b'{"error":"authority_unavailable"}'),
            lambda: http_error(409, b'{"error":"id_conflict"}'),
            lambda: http_error(403, b'{"error":"forbidden"}'),
            lambda: http_error(
                body=b"<html>Not found</html>", content_type="text/html"
            ),
            lambda: http_error(content_type=None),
            lambda: http_error(body=b'{"error":"unrelated"}'),
            lambda: http_error(body=b'{"error":"not_found","other":true}'),
            lambda: http_error(body=b'{"error":"not_found","error":"not_found"}'),
            lambda: http_error(body=b'{"error":"not_found"} trailing'),
            lambda: http_error(body=b" " * 4097 + b'{"error":"not_found"}'),
        ]
        for error in errors:
            with self.subTest(error=error):
                root, journal = self.fixture()
                queue = [self.job("original")]
                claims, sent = [], []
                failure = error()

                def transport(
                    operation: Any,
                    body: Any,
                    *,
                    claims: list[str] = claims,
                    failure: Exception = failure,
                    queue: list[dict[str, Any]] = queue,
                    sent: list[str] = sent,
                ) -> dict[str, Any]:
                    if operation == "claim":
                        claims.append(body["id"])
                        return {"job": queue.pop(0) if queue else None}
                    sent.append(canonical(body))
                    if len(sent) == 1:
                        raise failure
                    return {"id": "original", "attempt": 0, "status": "ready"}

                agent = StudioAgent(journal, transport)
                agent.poll()
                self.assertTrue(process_one(journal, lambda _: self.output()))
                with self.assertRaises(type(failure)):
                    agent.poll()
                self.assertEqual(len(claims), 1)
                self.assertEqual(agent.held_completions(), [])
                self.assertEqual(
                    journal.db.execute(
                        "SELECT acknowledged FROM studio_jobs"
                    ).fetchone()[0],
                    0,
                )
                journal.close()
                journal = Journal(root)
                self.addCleanup(journal.close)
                StudioAgent(journal, transport).poll()
                self.assertEqual(sent, [sent[0], sent[0]])
                self.assertFalse(
                    process_one(
                        journal, lambda _: self.fail("Output cannot be resampled")
                    )
                )
                if isinstance(failure, HTTPError):
                    failure.close()

    def test_explicit_retry_errors_preserve_hold_and_exact_body_without_claiming(
        self,
    ) -> None:
        for failure in ("scope_unavailable", "timeout", "wrong_ack", "invalid_status"):
            with self.subTest(failure=failure):
                _, journal = self.fixture()
                sent, claims = [], []

                def transport(
                    operation: Any,
                    body: Any,
                    *,
                    claims: list[str] = claims,
                    failure: str = failure,
                    sent: list[str] = sent,
                ) -> dict[str, Any]:
                    if operation == "claim":
                        claims.append(body["id"])
                        return {"job": self.job("held") if len(claims) == 1 else None}
                    sent.append(canonical(body))
                    if len(sent) == 1 or failure == "scope_unavailable":
                        raise http_error()
                    if failure == "timeout":
                        raise TimeoutError("retry acknowledgement unknown")
                    if failure == "invalid_status":
                        return {"id": "held", "attempt": 0, "status": "running"}
                    return {"id": "another-job", "attempt": 0, "status": "ready"}

                agent = StudioAgent(journal, transport)
                agent.poll()
                self.assertTrue(process_one(journal, lambda _: self.output()))
                agent.poll()
                original_hold = agent.held_completions()
                original_claims = list(claims)
                if failure == "scope_unavailable":
                    self.assertFalse(agent.retry_held("held", 0)["acknowledged"])
                else:
                    with self.assertRaises(
                        TimeoutError if failure == "timeout" else ValueError
                    ):
                        agent.retry_held("held", 0)
                self.assertEqual(agent.held_completions(), original_hold)
                self.assertEqual(claims, original_claims)
                self.assertEqual(sent, [sent[0], sent[0]])
                self.assertFalse(
                    process_one(
                        journal, lambda _: self.fail("Output cannot be resampled")
                    )
                )
                for identifier, attempt in (("held", 1), ("missing", 0)):
                    with self.assertRaisesRegex(ValueError, "held completion"):
                        agent.retry_held(identifier, attempt)

    def test_claim_scope_error_keeps_the_same_unknown_claim(self) -> None:
        _, journal = self.fixture()
        claims = []

        def transport(operation: Any, body: Any) -> Never:
            self.assertEqual(operation, "claim")
            claims.append(body["id"])
            raise http_error()

        agent = StudioAgent(journal, transport)
        for _ in range(2):
            with self.assertRaises(HTTPError) as raised:
                agent.poll()
            raised.exception.close()
        self.assertEqual(claims, [claims[0], claims[0]])
        self.assertEqual(agent.held_completions(), [])

    def test_rejected_retry_cannot_clear_an_unrelated_pending_claim(self) -> None:
        _, journal = self.fixture()

        def transport(operation: Any, body: Any) -> Never:
            self.assertEqual(operation, "rejected/result")
            raise http_error()

        agent = StudioAgent(journal, transport)
        body = canonical({"attempt": 0, "failure": "Rejected input", "held": True})
        with journal.db:
            journal.db.execute(
                "INSERT INTO studio_jobs VALUES(?,?,?, ?,0,?,?)",
                (
                    "rejected",
                    0,
                    "studio:rejected:0",
                    body,
                    "old-claim",
                    "scope_unavailable",
                ),
            )
            journal.db.execute("UPDATE studio_claim SET id='pending-live-claim'")
        self.assertFalse(agent.retry_held("rejected", 0)["acknowledged"])
        self.assertEqual(
            journal.db.execute("SELECT id FROM studio_claim").fetchone()[0],
            "pending-live-claim",
        )
        self.assertEqual(
            journal.db.execute(
                "SELECT body,rejected_claim FROM studio_jobs"
            ).fetchone(),
            (body, "old-claim"),
        )

    def test_upgrade_retains_existing_unacknowledged_body_and_local_output(
        self,
    ) -> None:
        root, journal = self.fixture(legacy=True)
        journal.submit("studio:existing:0", self.job("existing")["request"])
        self.assertTrue(process_one(journal, lambda _: self.output()))
        body = canonical({"attempt": 0, "result": self.output()})
        with journal.db:
            journal.db.execute(
                "INSERT INTO studio_jobs VALUES(?,?,?,?,0,NULL)",
                ("existing", 0, "studio:existing:0", body),
            )
        original = journal.result("studio:existing:0")
        journal.close()
        journal = Journal(root)
        self.addCleanup(journal.close)
        agent = StudioAgent(journal, lambda *_: self.fail("Migration cannot send"))
        self.assertEqual(
            journal.db.execute(
                "SELECT body,acknowledged,completion_hold FROM studio_jobs"
            ).fetchone(),
            (body, 0, None),
        )
        self.assertEqual(journal.result("studio:existing:0"), original)
        self.assertEqual(agent.held_completions(), [])
        self.assertFalse(
            process_one(journal, lambda _: self.fail("Migration cannot resample"))
        )

    def test_explicit_duplicate_recovery_accepts_the_platform_compiler_next_attempt(
        self,
    ) -> None:
        _, journal = self.fixture()
        agent = StudioAgent(
            journal,
            lambda operation, body: {
                "id": "compiled",
                "attempt": 1,
                "status": "queued",
            },
        )
        body = canonical({"attempt": 0, "result": self.output()})
        with journal.db:
            journal.db.execute(
                "INSERT INTO studio_jobs VALUES(?,?,?, ?,0,NULL,?)",
                ("compiled", 0, "studio:compiled:0", body, "scope_unavailable"),
            )
        self.assertTrue(agent.retry_held("compiled", 0)["acknowledged"])
        self.assertEqual(
            journal.db.execute(
                "SELECT body,attempt,acknowledged FROM studio_jobs"
            ).fetchone(),
            (body, 0, 1),
        )

    def test_operator_list_and_one_shot_retry_use_the_owner_lock_and_never_run_a_model(
        self,
    ) -> None:
        root, journal = self.fixture()
        StudioAgent(journal, lambda *_: self.fail("Preparation has no transport"))
        body = canonical({"attempt": 0, "result": self.output()})
        with journal.db:
            journal.db.execute(
                "INSERT INTO studio_jobs VALUES(?,?,?, ?,0,NULL,?)",
                ("held", 0, "studio:held:0", body, "scope_unavailable"),
            )
        journal.close()
        sent = []
        mode = "success"

        def transport(operation: Any, value: Any) -> dict[str, Any]:
            self.assertEqual(operation, "held/result")
            sent.append(canonical(value))
            if mode == "timeout":
                raise TimeoutError("unknown acknowledgement")
            if mode == "unavailable":
                raise http_error()
            return {"id": "held", "attempt": 0, "status": "ready"}

        def run(*arguments: Any) -> tuple[Any, ...]:
            output = io.StringIO()
            with redirect_stdout(output):
                try:
                    author_service.main(
                        ["--config", str(root / "unused.json"), *arguments]
                    )
                except SystemExit as error:
                    return error.code, json.loads(output.getvalue())
            return 0, json.loads(output.getvalue())

        with (
            patch.object(
                author_service, "load_config", return_value={"state": str(root)}
            ),
            patch.object(author_service, "transport", return_value=transport),
            patch.object(
                author_service, "generate", side_effect=AssertionError("No model call")
            ),
            patch.object(
                author_service,
                "process_one",
                side_effect=AssertionError("No queued model work"),
            ),
            patch.object(author_service.signal, "signal"),
            patch.object(author_service.os, "umask"),
        ):
            code, listing = run("--list-held")
            self.assertEqual(code, 0)
            self.assertEqual(listing["held"][0]["status"], "scope_unavailable")
            self.assertFalse(listing["held"][0]["acknowledged"])
            self.assertNotIn(self.output()["source"], json.dumps(listing))
            self.assertEqual(sent, [])
            with (root / "service.owner").open("a+b") as owner:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError, "already running"):
                    run("--retry-held", "held", "--attempt", "0")
                self.assertEqual(sent, [])
            for retry_mode, expected_code in (("timeout", 1), ("unavailable", 2)):
                mode = retry_mode
                code, result = run("--retry-held", "held", "--attempt", "0")
                self.assertEqual(code, expected_code)
                self.assertFalse(result["acknowledged"])
                self.assertEqual(run("--list-held")[1], listing)
            mode = "success"
            code, result = run("--retry-held", "held", "--attempt", "0")
            self.assertEqual(code, 0)
            self.assertTrue(result["acknowledged"])
            self.assertEqual(run("--list-held"), (0, {"held": []}))
            self.assertEqual(sent, [body, body, body])
        journal = Journal(root)
        self.addCleanup(journal.close)
        self.assertEqual(
            journal.db.execute(
                "SELECT body,attempt,acknowledged,completion_hold FROM studio_jobs"
            ).fetchone(),
            (body, 0, 1, None),
        )

    def test_service_restart_reports_durable_holds_without_saved_content(self) -> None:
        root, journal = self.fixture()
        StudioAgent(journal, lambda *_: self.fail("Preparation has no transport"))
        body = canonical({"attempt": 0, "result": self.output()})
        with journal.db:
            journal.db.execute(
                "INSERT INTO studio_jobs VALUES(?,?,?, ?,0,NULL,?)",
                ("held", 0, "studio:held:0", body, "scope_unavailable"),
            )
        journal.close()
        with (
            patch.object(
                author_service, "load_config", return_value={"state": str(root)}
            ),
            patch.object(
                author_service,
                "transport",
                return_value=lambda *_: self.fail("No dispatch on startup"),
            ),
            patch.object(
                author_service,
                "process_one",
                side_effect=AssertionError("No model call"),
            ),
            patch.object(author_service.signal, "signal"),
            patch.object(author_service.os, "umask"),
            patch.object(author_service.threading, "Event") as event,
        ):
            event.return_value.is_set.return_value = True
            for _ in range(2):
                output = io.StringIO()
                with redirect_stdout(output):
                    author_service.main(["--config", str(root / "unused.json")])
                events = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertEqual(events[0], {"event": "protocol_author_ready"})
                self.assertEqual(
                    events[1],
                    {
                        "event": "protocol_author_completion_held",
                        "job": "held",
                        "attempt": 0,
                        "local_id": "studio:held:0",
                        "status": "scope_unavailable",
                        "acknowledged": False,
                    },
                )
                self.assertEqual(len(events), 2)
