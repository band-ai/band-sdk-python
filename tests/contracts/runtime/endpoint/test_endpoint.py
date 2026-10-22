from __future__ import annotations

from typing import Any, Never

import pytest

from remote_agent.store import Store
from tests.contracts.runtime.endpoint.kit import EndpointFixture


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestEndpoint(EndpointFixture):
    def test_authenticated_notification_then_ordered_admission_runs_program_without_waking_the_model(
        self,
    ) -> None:
        event = self.question()
        code, result = self.post("/events", event, "r" * 32)
        self.assertEqual((code, result), (200, {"status": "notified"}))
        self.assertIsNone(self.store.get("input:" + event["id"]))
        self.assertEqual(self.agent.receive(event)["producer"], "program")
        self.assertEqual(self.provider.calls, [])

    def test_control_wakes_model_once_and_duplicate_is_durable_across_restart(
        self,
    ) -> None:
        command = {
            "id": "wake1",
            "action": "wake",
            "reason": "Room participant noticed a questionable answer",
        }
        code, result = self.post("/controls", command)
        self.assertEqual((code, result["status"]), (200, "completed"))
        self.assertEqual(self.provider.calls, ["wake"])
        self.assertEqual(self.post("/controls", command), (code, result))
        self.endpoint.close()
        self.agent.close()
        self.store.close()
        self.store = Store(self.path / "agent.sqlite3")
        self.addCleanup(self.store.close)
        self.agent = self.make_agent()
        self.assertEqual(self.agent.control("user", command), result)
        self.assertEqual(self.provider.calls, ["wake"])

    def test_takeover_and_verified_resume_change_future_routing(self) -> None:
        self.assertEqual(
            self.post(
                "/controls",
                {"id": "take", "action": "takeover", "reason": "Human requests review"},
            )[0],
            200,
        )
        event = self.question()
        self.assertEqual(
            self.post("/events", event, "r" * 32), (200, {"status": "notified"})
        )
        self.assertEqual(self.agent.receive(event)["producer"], "llm")
        self.assertEqual(
            self.post(
                "/controls",
                {"id": "resume", "action": "resume", "reason": "Review complete"},
            )[0],
            200,
        )
        event = self.question()
        self.assertEqual(
            self.post("/events", event, "r" * 32), (200, {"status": "notified"})
        )
        self.assertEqual(self.agent.receive(event)["producer"], "program")

    def test_invalid_token_missing_grant_and_actor_spoof_do_not_wake_model(
        self,
    ) -> None:
        command = {"id": "x", "action": "takeover", "reason": "request"}
        self.assertEqual(self.post("/controls", command, "invalid")[0], 401)
        self.assertEqual(self.post("/controls", command, "o" * 32)[0], 403)
        self.assertEqual(
            self.post("/controls", dict(command, actor="user"), "o" * 32)[0], 400
        )
        self.assertEqual(self.post("/events", self.question(), "u" * 32)[0], 403)
        self.assertEqual(self.provider.calls, [])

    def test_conflicting_control_duplicate_is_rejected_without_second_call(
        self,
    ) -> None:
        command = {"id": "x", "action": "wake", "reason": "inspect"}
        self.assertEqual(self.post("/controls", command)[0], 200)
        self.assertEqual(
            self.post("/controls", dict(command, reason="different"))[0], 409
        )
        self.assertEqual(self.provider.calls, ["wake"])

    def test_model_cannot_expand_the_control_callers_grant(self) -> None:
        self.provider.wake = lambda bundle: {
            "action": "takeover",
            "note": "Want control",
        }
        code, result = self.post(
            "/controls", {"id": "x", "action": "wake", "reason": "inspect"}, "o" * 32
        )
        self.assertEqual(code, 202)
        self.assertEqual(result["status"], "held")
        self.assertEqual(self.store.get("mode"), "program")

    def test_request_size_and_strict_json_object_are_checked_before_dispatch(
        self,
    ) -> None:
        self.assertEqual(self.post("/controls", ["not an object"])[0], 400)
        self.assertEqual(
            self.post("/controls", {"id": "x", "action": "wake", "reason": 1.5})[0], 400
        )
        command = {"id": "x", "action": "wake", "reason": "x" * 70_000}
        self.assertEqual(self.post("/controls", command)[0], 413)
        self.assertEqual(self.provider.calls, [])

    def test_failed_wake_is_recorded_and_duplicate_does_not_repeat_it(self) -> None:
        calls = []

        def unavailable(bundle: Any) -> Never:
            calls.append(bundle["control"]["id"])
            raise RuntimeError("model endpoint unavailable")

        self.provider.wake = unavailable
        command = {"id": "offline", "action": "wake", "reason": "inspect"}
        first = self.post("/controls", command)
        self.assertEqual(first[0], 202)
        self.assertEqual(self.post("/controls", command), first)
        self.assertEqual(calls, ["offline"])

    def test_repair_cannot_override_a_newer_explicit_takeover(self) -> None:
        self.provider.candidate = self.artifact
        repair = {"id": "repair", "action": "repair", "reason": "Review common cases"}
        self.assertEqual(self.post("/controls", repair)[0], 200)
        self.assertTrue(self.provider.repair_started.wait(2))
        takeover = {"id": "take", "action": "takeover", "reason": "Stay with the LLM"}
        self.assertEqual(self.post("/controls", takeover)[0], 200)
        self.provider.release_repair.set()
        self.assertTrue(self.agent.wait_for_repair(3))
        self.assertEqual(self.store.get("repair")["status"], "installed")
        self.assertEqual(self.store.get("mode"), "llm")
