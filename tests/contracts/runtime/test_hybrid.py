from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from queue import Empty
from types import SimpleNamespace
from typing import Any, Never
from unittest.mock import patch

import pytest

from remote_agent.controller import AgentError
from remote_agent.program import Artifact
from remote_agent.store import Store
from remote_agent.transport import TransportError
from tests.contracts.runtime.callbackdeadline import CALLBACK_LOOP, callback_deadline
from tests.contracts.runtime.hybrid import HybridFixture
from tests.contracts.runtime.program import SOURCE
from tests.contracts.runtime.required import require_value
from tests.contracts.runtime.timers import DeadlineQueue, TimerClock


class TestHybrid(HybridFixture):
    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_retained_history_fences_expired_inputs_and_restores_from_the_anchor(
        self,
    ) -> None:
        agent = self.start(max_model_calls=0)
        events = [self.question()]
        agent.receive(events[0])
        for _ in range(4):
            events.append(self.question())
            self.assertEqual(agent.receive(events[-1])["producer"], "program")
        self.store.retain(target_bytes=1, minimum_steps=2)
        turns = self.store.get("turns")
        self.assertEqual(
            agent.receive(events[0]),
            {"status": "expired", "reason": "outside_retained_history"},
        )
        self.assertEqual(
            agent.notify(events[0]),
            {"status": "expired", "reason": "outside_retained_history"},
        )
        self.assertEqual(self.store.get("turns"), turns)
        agent.close()
        self.store.close()
        self.store = Store(self.path / "agent.sqlite3")
        self.addCleanup(self.store.close)
        restored = self.start(max_model_calls=0)
        self.assertEqual(restored.receive(events[-1])["producer"], "program")
        self.assertEqual(restored.receive(self.question())["producer"], "program")
        self.assertEqual(self.store.get("state"), {"count": 6})
        self.assertEqual(self.provider.calls, [])

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_a_session_ending_during_model_work_discards_the_late_answer_without_resampling(
        self,
    ) -> None:
        clock = [0]
        agent = self.start()
        agent.now = lambda: clock[0]
        original = self.provider.respond

        def late(bundle: Any) -> Any:
            result = original(bundle)
            clock[0] = 1_000_000_000
            return result

        self.provider.respond = late
        result = agent.receive(self.question("needs judgment"))
        self.assertEqual(result["status"], "completed")
        self.assertIsNone(result["receipt"])
        self.assertEqual(self.store.get("state"), {"count": 0})
        self.assertEqual(self.store.get("model_calls"), 1)
        self.assertEqual(
            self.room.view("agent", clock[0])["view"]["status"], "terminal"
        )

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_common_case_uses_no_model_and_atomically_snapshots_the_accepted_turn(
        self,
    ) -> None:
        agent = self.start()
        event = self.question()
        result = agent.receive(event)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["producer"], "program")
        self.assertEqual(result["receipt"]["payload"]["text"], "pong")
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(
            require_value(
                self.store.latest_snapshot(), "Expected a committed snapshot"
            )["state"],
            {"count": 1},
        )
        self.assertEqual(agent.receive(event), result)
        self.assertEqual(self.store.get("turns"), 1)
        self.assertNotIn("initial_route", self.store.get("config"))

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_initial_model_route_preserves_the_qualified_program_and_later_resume_across_restart(
        self,
    ) -> None:
        options = {
            "initial_route": "llm",
            "validator": lambda *args: {"ok": True, "checks": ["fixture semantics"]},
            "control_actors": {"operator": ["resume"]},
        }
        agent = self.start(**options)
        self.assertEqual(self.store.get("mode"), "llm")
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(agent.receive(self.question())["producer"], "llm")
        resumed = agent.control(
            "operator",
            {
                "id": "resume-initial-route",
                "action": "resume",
                "reason": "Use the qualified program",
            },
        )
        self.assertEqual(resumed["status"], "completed")
        agent.close()
        self.store.close()
        self.store = Store(self.path / "agent.sqlite3")
        self.addCleanup(self.store.close)
        restored = self.start(**options)
        self.assertEqual(self.store.get("mode"), "program")
        self.assertEqual(restored.receive(self.question())["producer"], "program")
        self.assertEqual(self.provider.calls, ["respond"])

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_initial_route_cannot_force_a_program_that_declares_model_only_coverage(
        self,
    ) -> None:
        with self.assertRaisesRegex(AgentError, "initial.*route"):
            self.start(initial_route="unknown")
        with self.assertRaisesRegex(AgentError, "LLM-only"):
            self.start(
                SOURCE + '\nCOVERAGE = dict(COVERAGE, mode="llm_only")\n',
                initial_route="program",
            )

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_model_can_query_bounded_older_history_without_resampling_completed_turns(
        self,
    ) -> None:
        agent = self.start()
        agent.receive(self.question())
        original = self.provider.respond

        def querying(bundle: Any) -> Any:
            if not bundle.get("history_pages"):
                self.provider.calls.append("history_query")
                return {
                    "history_query": {
                        "after": 0,
                        "limit": 3,
                        "kind": "turn",
                        "query": "ping",
                    }
                }
            self.assertEqual(len(bundle["history_pages"][0]["records"]), 1)
            self.assertEqual(bundle["history_pages"][0]["records"][0]["kind"], "turn")
            return original(bundle)

        self.provider.respond = querying
        event = self.question("needs judgment and earlier context")
        result = agent.receive(event)
        self.assertEqual(result["producer"], "llm")
        self.assertEqual(self.store.get("model_calls"), 2)
        self.assertEqual(agent.receive(event), result)
        self.assertEqual(self.store.get("model_calls"), 2)
        self.assertTrue(any(r["kind"] == "history_query" for r in self.store.records()))

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_history_query_budget_rejects_the_attempt_without_pinning_the_input(
        self,
    ) -> None:
        agent = self.start(max_attempts=1)

        def querying(bundle: Any) -> dict[str, Any]:
            return {
                "history_query": {"after": 0, "limit": 1, "kind": "turn", "query": ""}
            }

        self.provider.respond = querying
        result = agent.receive(self.question("needs context"))
        self.assertEqual(result["status"], "held")
        self.assertIn("attempt budget exhausted", result["reason"])
        self.assertIsNone(self.store.get("execution"))
        self.assertEqual(self.store.get("model_calls"), 3)
        self.assertEqual(agent.recover()["status"], "held")
        self.assertEqual(self.store.get("model_calls"), 3)
        self.assertEqual(
            len([r for r in self.store.records() if r["kind"] == "history_query"]), 2
        )

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_a_model_turn_snapshots_its_entire_committed_state_immediately(
        self,
    ) -> None:
        agent = self.start()
        result = agent.receive(self.question("requires judgment"))
        self.assertEqual(result["producer"], "llm")
        self.assertEqual(
            require_value(
                self.store.latest_snapshot(), "Expected a committed snapshot"
            )["state"],
            self.store.get("state"),
        )
        self.assertEqual(
            require_value(
                self.store.latest_snapshot(), "Expected a committed snapshot"
            )["state"],
            {"count": 1},
        )

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_freeform_delegates_one_turn_then_next_known_case_returns_to_program(
        self,
    ) -> None:
        agent = self.start()
        first = agent.receive(
            self.question("Explain this ambiguous English instruction")
        )
        second = agent.receive(self.question())
        self.assertEqual([first["producer"], second["producer"]], ["llm", "program"])
        self.assertEqual(self.provider.calls, ["respond"])
        self.assertEqual(self.store.get("state"), {"count": 2})
        bundle = self.provider.bundles[0]
        self.assertIn("judgment", bundle["view"]["definition"]["role"]["meta"]["doc"])
        self.assertNotIn("checkpoint", bundle)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_explicit_ongoing_handoff_persists_across_restart(self) -> None:
        source = SOURCE.replace(
            'Delegate("Requires language judgment")',
            'Delegate("Requires language judgment", scope="ongoing")',
        )
        agent = self.start(source)
        agent.receive(self.question("needs ongoing judgment"))
        agent.close()
        self.store.close()
        self.store = Store(self.path / "agent.sqlite3")
        self.addCleanup(self.store.close)
        agent = self.start(source)
        result = agent.receive(self.question())
        self.assertEqual(result["producer"], "llm")
        self.assertEqual(self.store.get("mode"), "llm")

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_wrong_turn_program_reply_is_blocked_before_room_publication_and_llm_takes_over(
        self,
    ) -> None:
        source = SOURCE.replace('Message("answer"', 'Message("question"')
        agent = self.start(source, max_repairs=0)
        result = agent.receive(self.question())
        self.assertEqual(result["producer"], "llm")
        facts = self.room.view("user", 0)["view"]["facts"]
        self.assertEqual([f["kind"] for f in facts.values()].count("answer"), 1)
        self.assertEqual([f["kind"] for f in facts.values()].count("question"), 1)
        self.assertEqual(self.store.get("state"), {"count": 1})
        self.assertEqual(self.store.get("mode"), "llm")
        self.assertIn("local_contract", self.provider.bundles[0]["fault"]["reason"])

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_crash_and_timeout_invoke_llm_for_the_same_input(self) -> None:
        for body in ["raise RuntimeError('broken')", CALLBACK_LOOP]:
            with self.subTest(body=body):
                source = (
                    SOURCE + "\ndef handle(event, state, context):\n    " + body + "\n"
                )
                if self.agent:
                    self.agent.close()
                    self.store.close()
                    self.store = Store(self.path / ("next.sqlite3"))
                    self.addCleanup(self.store.close)
                agent = self.start(source, max_repairs=0)
                with (
                    callback_deadline()
                    if body == CALLBACK_LOOP
                    else nullcontext() as deadline
                ):
                    self.assertEqual(agent.receive(self.question())["producer"], "llm")
                    if deadline is not None:
                        self.assertTrue(deadline.entered and deadline.expired)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_background_repair_does_not_block_llm_turns_and_only_installs_validated_candidate(
        self,
    ) -> None:
        broken = SOURCE.replace('Message("answer"', 'Message("question"')
        self.provider.candidate = self.initial
        agent = self.start(
            broken,
            validator=lambda candidate, bundle: {
                "ok": True,
                "checks": ["fixture answer semantics"],
            },
        )
        self.assertEqual(agent.receive(self.question())["producer"], "llm")
        self.assertTrue(self.provider.repair_started.wait(2))
        # The repair callback is still waiting while this second turn is completed.
        second = agent.receive(self.question())
        self.assertEqual(second["producer"], "llm")
        self.assertFalse(self.provider.release_repair.is_set())
        self.provider.release_repair.set()
        self.assertTrue(agent.wait_for_repair(3))
        self.assertEqual(self.store.get("program")["sha256"], self.initial.sha256)
        self.assertEqual(agent.receive(self.question())["producer"], "program")
        self.assertEqual(self.store.get("state"), {"count": 3})

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_incompatible_candidate_stays_rejected_while_llm_keeps_serving(
        self,
    ) -> None:
        broken = SOURCE.replace('Message("answer"', 'Message("question"')
        incompatible = SOURCE.replace('STATE_VERSION = "1"', 'STATE_VERSION = "2"')
        self.provider.candidate = Artifact.publish(
            incompatible, self.path / "artifacts"
        )
        self.provider.release_repair.set()
        agent = self.start(
            broken,
            validator=lambda candidate, bundle: {"ok": True, "checks": ["fixture"]},
        )
        agent.receive(self.question())
        self.assertTrue(agent.wait_for_repair(3))
        self.assertEqual(self.store.get("mode"), "llm")
        self.assertEqual(self.store.get("repair")["status"], "rejected")
        self.assertEqual(agent.receive(self.question())["producer"], "llm")

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_unknown_publication_is_reconciled_without_repeating_llm_or_state_transition(
        self,
    ) -> None:
        agent = self.start()
        event = self.question("requires language judgment")
        original = self.room.publish

        def lose_ack(*args: Any) -> Never:
            original(*args)
            raise TransportError("lost acknowledgement")

        with patch.object(self.room, "publish", lose_ack):
            result = agent.receive(event)
            self.assertEqual(result["status"], "held")
            self.assertEqual(self.store.get("state"), {"count": 0})
            self.assertIsNotNone(self.store.get("staged"))
        agent.close()
        self.store.close()
        self.store = Store(self.path / "agent.sqlite3")
        self.addCleanup(self.store.close)
        agent = self.start()
        result = agent.recover()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.provider.calls, ["respond"])
        self.assertEqual(self.store.get("state"), {"count": 1})
        self.assertEqual(agent.receive(event)["status"], "completed")

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_room_rejection_does_not_commit_program_state_and_fallback_is_checked_again(
        self,
    ) -> None:
        agent = self.start()
        original = self.room.publish
        once = [True]

        def reject_first(message: Any, actor: Any, now: Any) -> Any:
            if once.pop() if once else False:
                return {"status": "rejected", "reason": "stale_version"}
            return original(message, actor, now)

        self.room.publish = reject_first
        # Receive an already accepted request before replacing only the outgoing path.
        event = original(
            {
                "id": "q",
                "session": "s",
                "kind": "question",
                "payload": {"text": "ping"},
                "expected_version": 0,
            },
            "user",
            0,
        )
        self.assertEqual(agent.receive(event)["producer"], "llm")
        self.assertEqual(self.store.get("state"), {"count": 1})

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_untrusted_input_and_invalid_llm_reply_do_not_reach_the_room(self) -> None:
        agent = self.start(max_repairs=0)
        event = self.question("open ended")
        forged = dict(event, payload={"text": "forged"})
        with self.assertRaises(TransportError):
            agent.receive(forged)
        self.provider.invalid = True
        result = agent.receive(event)
        self.assertEqual(result["status"], "held")
        self.assertEqual(self.room.view("agent", 0)["view"]["version"], 1)
        self.assertEqual(self.store.get("state"), {"count": 0})

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_model_failure_and_budget_exhaustion_leave_the_input_pending(self) -> None:
        agent = self.start(max_model_calls=1)
        self.provider.failure = True
        result = agent.receive(self.question("needs judgment"))
        self.assertEqual(result["status"], "held")
        self.assertEqual(agent.recover()["status"], "held")
        self.assertEqual(self.provider.calls, ["respond"])
        self.assertEqual(self.store.get("model_calls"), 1)
        self.assertEqual(len(self.store.get("queue")), 1)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_unsuitable_replacement_declares_llm_only_and_never_runs_the_program_handler(
        self,
    ) -> None:
        source = SOURCE.replace('"mode": "partial"', '"mode": "llm_only"')
        source += "\ndef handle(*args):\n    raise RuntimeError('must never run')\n"
        agent = self.start(source)
        self.assertEqual(agent.receive(self.question())["producer"], "llm")
        self.assertEqual(self.store.get("repair_calls"), 0)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_failed_atomic_snapshot_after_room_ack_is_reconciled_without_duplicate_reply(
        self,
    ) -> None:
        agent = self.start()
        event = self.question()
        with patch.object(
            self.store, "_snapshot", side_effect=RuntimeError("snapshot fault")
        ):
            self.assertEqual(agent.receive(event)["status"], "held")
        self.assertEqual(self.store.get("state"), {"count": 0})
        self.assertEqual(self.store.get("turns"), 0)
        self.assertEqual(self.room.view("user", 0)["view"]["version"], 2)
        self.assertEqual(agent.recover()["status"], "completed")
        self.assertEqual(self.store.get("state"), {"count": 1})
        self.assertEqual(self.room.view("user", 0)["view"]["version"], 2)
        self.assertEqual(self.provider.calls, [])

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine
    def test_unsupported_non_idempotent_room_is_rejected_before_initialization(
        self,
    ) -> None:
        self.room.idempotent = False
        with self.assertRaisesRegex(AgentError, "Idempotent room authority required"):
            self.start()
        self.assertIsNone(self.store.get("config"))
        self.assertFalse(
            any(r["kind"] == "program_input" for r in self.store.records())
        )

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_late_model_answer_is_ignored_after_timeout(self) -> None:
        released = threading.Event()
        entered = threading.Event()
        clock = TimerClock()
        original = self.provider.respond

        def stalled(bundle: Any) -> Any:
            entered.set()
            if not released.wait(5):
                raise AssertionError("The late model callback was not released")
            return original(bundle)

        self.provider.respond = stalled
        agent = self.start(model_timeout=10)
        event = self.question("judgment")
        queues = SimpleNamespace(
            Queue=lambda maxsize: DeadlineQueue(clock, maxsize), Empty=Empty
        )
        with (
            patch("remote_agent.controller.queue", queues),
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            pending = executor.submit(agent.receive, event)
            try:
                self.assertTrue(entered.wait(5))
                clock.await_timer()
                clock.advance(9)
                self.assertFalse(pending.done())
                clock.advance(1)
                self.assertEqual(pending.result(timeout=5)["status"], "held")
                self.assertTrue(agent.jobs["respond"].is_alive())
            finally:
                released.set()
                agent.jobs["respond"].join(5)
        self.assertFalse(agent.jobs["respond"].is_alive())
        self.assertEqual(self.store.get("state"), {"count": 0})
        self.assertEqual(self.room.view("agent", 0)["view"]["version"], 1)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_candidate_cannot_rewrite_an_answer_already_committed_by_the_llm(
        self,
    ) -> None:
        broken = SOURCE.replace('Message("answer"', 'Message("question"')
        divergent = SOURCE.replace('"text": "pong"', '"text": "different"')
        self.provider.candidate = Artifact.publish(divergent, self.path / "artifacts")
        self.provider.release_repair.set()
        agent = self.start(
            broken,
            validator=lambda candidate, bundle: {"ok": True, "checks": ["fixture"]},
        )
        agent.receive(self.question())
        self.assertTrue(agent.wait_for_repair(3))
        self.assertEqual(self.store.get("repair")["status"], "rejected")
        self.assertIn("committed behavior", self.store.get("repair")["reason"])
        self.assertEqual(self.store.get("state"), {"count": 1})

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_candidate_is_probed_against_a_fault_when_no_llm_answer_has_completed(
        self,
    ) -> None:
        broken = SOURCE.replace('Message("answer"', 'Message("question"')
        self.provider.failure = True
        self.provider.candidate = Artifact.publish(broken, self.path / "artifacts")
        self.provider.release_repair.set()
        agent = self.start(
            broken,
            validator=lambda candidate, bundle: {"ok": True, "checks": ["fixture"]},
        )
        agent.receive(self.question())
        self.assertTrue(agent.wait_for_repair(3))
        self.assertEqual(self.store.get("repair")["status"], "rejected")
        self.assertEqual(self.store.get("mode"), "llm")
