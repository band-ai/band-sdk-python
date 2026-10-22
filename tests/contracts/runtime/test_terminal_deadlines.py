from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import patch

import pytest

from remote_agent.authority import DurableRoom
from remote_agent.band.arithmetic import RuntimeDriver
from remote_agent.band.generic import GenericRuntimeDriver
from remote_agent.band.serve_programs import assessor, create_runtime
from remote_agent.network import RoomAPI
from remote_agent.program import Program
from remote_agent.store import Store
from remote_agent.transport import TransportError
from tests.contracts.runtime.bandrolequality import SOURCE, policy
from tests.contracts.runtime.required import require_value
from tests.contracts.runtime.terminaldeadlines import (
    CONTRACT,
    DONE,
    EXHAUSTED,
    INITIAL,
    SILENT,
    NoListener,
    TerminalDeadlineFixture,
)


@pytest.mark.contract_isolation
@pytest.mark.contract_engine("band")
class TestTerminalDeadline(TerminalDeadlineFixture):
    def test_delayed_first_input_finishes_after_real_deadline_and_checkpoint_restore(
        self,
    ) -> None:
        agent = self.start()
        event = self.request()
        view = self.expire()
        packet = self.authority.request(op="checkpoint")
        self.authority.request(
            op="restore",
            checkpoint=packet,
            now=self.clock[0],
            capabilities=self.room.capabilities,
        )
        self.assertEqual(
            self.room.view("agent", self.clock[0])["view"]["obligations"],
            view["obligations"],
        )
        execute = Program.execute
        with (
            patch.object(
                Program, "execute", autospec=True, side_effect=execute
            ) as callbacks,
            patch.object(self.room, "publish", wraps=self.room.publish) as publish,
            patch.object(self.room, "lookup", wraps=self.room.lookup) as lookup,
        ):
            self.assert_finished(agent, event, view, agent.receive(event))
            callbacks.assert_not_called()
            publish.assert_not_called()
            lookup.assert_not_called()
            self.assertIsNone(self.store.get("fault"))
            command = {
                "id": "retry-completed",
                "action": "retry",
                "reason": "Inspect terminal progress",
            }
            result = agent.control("operator", command)
            self.assertEqual(result, {"status": "idle"})
            self.assertEqual(agent.control("operator", command), result)
            agent.close()
            self.store.close()
            self.store = Store(self.root / "agent.sqlite3")
            self.addCleanup(self.store.close)
            restored = self.start()
            self.assertEqual(restored.recover(), {"status": "idle"})
            self.assertEqual(restored.receive(event), DONE)
            self.assertEqual(restored.control("operator", command), result)
            callbacks.assert_not_called()

    def test_session_cap_after_a_prior_accepted_answer_still_finishes_without_callbacks(
        self,
    ) -> None:
        agent = self.start()
        event = self.request()
        accepted = self.room.publish(
            {
                "id": "prior-answer",
                "session": "s",
                "kind": "normalized",
                "payload": {"request": event["id"], "text": "BAND"},
                "expected_version": 1,
            },
            "agent",
            0,
        )
        self.assertEqual(accepted["status"], "accepted")
        view = self.expire(now=1800000, outcome="expired", diagnostics=False)
        execute = Program.execute
        with (
            patch.object(
                Program, "execute", autospec=True, side_effect=execute
            ) as callbacks,
            patch.object(self.room, "publish", wraps=self.room.publish) as publish,
        ):
            self.assert_finished(agent, event, view, agent.receive(event))
            callbacks.assert_not_called()
            publish.assert_not_called()

    def test_expiry_during_callback_discards_the_proposed_state_without_publication(
        self,
    ) -> None:
        agent = self.start()
        event = self.request()
        execute = Program.execute

        def finish_after_deadline(program: Any, request: Any, **options: Any) -> Any:
            result = execute(program, request, **options)
            self.clock[0] = 120000
            return result

        with (
            patch.object(
                Program, "execute", autospec=True, side_effect=finish_after_deadline
            ) as callbacks,
            patch.object(self.room, "publish", wraps=self.room.publish) as publish,
        ):
            result = agent.receive(event)
            view = self.expire()
            self.assert_finished(agent, event, view, result, attempts=1)
            self.assertEqual(callbacks.call_count, 1)
            publish.assert_not_called()
            self.assertIsNone(self.store.get("fault"))

    def test_authority_terminal_rejection_finishes_the_staged_input_without_a_fault(
        self,
    ) -> None:
        agent = self.start()
        event = self.request()
        publish = self.room.publish
        decisions = []

        def expire_before_publication(*args: Any) -> Any:
            self.clock[0] = 120000
            decision = publish(args[0], args[1], self.clock[0])
            decisions.append(decision)
            return decision

        with patch.object(
            self.room, "publish", side_effect=expire_before_publication
        ) as sends:
            result = agent.receive(event)
            view = self.expire()
            self.assertEqual(decisions, [{"status": "rejected", "reason": "terminal"}])
            self.assert_finished(agent, event, view, result, attempts=1)
            self.assertEqual(sends.call_count, 1)
            self.assertIsNone(self.store.get("fault"))

    def test_active_ordinary_duty_publishes_once_and_duplicate_input_keeps_state(
        self,
    ) -> None:
        agent = self.start()
        event = self.request()
        view = self.room.view("agent", 0)["view"]
        self.assertEqual(view["status"], "active")
        self.assertTrue(view["obligations"])
        execute = Program.execute
        with (
            patch.object(
                Program, "execute", autospec=True, side_effect=execute
            ) as callbacks,
            patch.object(self.room, "publish", wraps=self.room.publish) as publish,
        ):
            result = agent.receive(event)
            self.assertEqual(
                (result["status"], result["producer"]), ("completed", "program")
            )
            self.assertEqual(
                result["receipt"]["payload"], {"request": event["id"], "text": "BAND"}
            )
            self.assertEqual(agent.receive(event), result)
            self.assertEqual(self.store.get("state"), {"processed": 1, "mode": "ascii"})
            self.assertEqual(self.store.get("turns"), 1)
            self.assertEqual(self.provider.calls, [])
            self.assertEqual((callbacks.call_count, publish.call_count), (1, 1))

    def test_active_unanswered_duty_keeps_the_exhausted_budget_and_pending_input(
        self,
    ) -> None:
        self.assertNotEqual(SILENT, SOURCE)
        agent = self.start(SILENT)
        event = self.request()
        self.assertEqual(agent.receive(event), EXHAUSTED)
        self.assertEqual(self.room.view("agent", 0)["view"]["status"], "active")
        self.assertEqual(
            self.store.get("fault")["reason"],
            "local_contract: response obligation left unanswered",
        )
        execute = Program.execute
        with patch.object(
            Program, "execute", autospec=True, side_effect=execute
        ) as callbacks:
            self.assertEqual(agent.recover(), EXHAUSTED)
            command = {
                "id": "retry-active",
                "action": "retry",
                "reason": "Preserve the live duty budget",
            }
            self.assertEqual(agent.control("operator", command), EXHAUSTED)
            callbacks.assert_not_called()
        self.assertEqual(self.store.get("queue"), [event["id"]])
        self.assertEqual(self.store.get("attempts:" + event["id"]), 1)
        self.assertEqual(self.store.get("state"), INITIAL)
        self.assertEqual(self.store.get("model_calls"), 0)

    def test_recovery_finishes_exhausted_active_work_when_its_ordinary_duty_later_expires(
        self,
    ) -> None:
        agent = self.start(SILENT)
        event = self.request()
        self.assertEqual(agent.receive(event), EXHAUSTED)
        self.assertEqual(
            self.store.get("fault")["reason"],
            "local_contract: response obligation left unanswered",
        )
        view = self.expire()
        agent.close()
        self.store.close()
        self.store = Store(self.root / "agent.sqlite3")
        self.addCleanup(self.store.close)
        restored = self.start(SILENT)
        execute = Program.execute
        with patch.object(
            Program, "execute", autospec=True, side_effect=execute
        ) as callbacks:
            command = {
                "id": "retry-expired",
                "action": "retry",
                "reason": "Recheck the expired duty",
            }
            result = restored.control("operator", command)
            self.assert_finished(restored, event, view, result, attempts=1)
            self.assertEqual(restored.control("operator", command), result)
            callbacks.assert_not_called()

    def test_accepted_unknown_publication_reconciles_its_exact_id_before_terminal_cleanup(
        self,
    ) -> None:
        agent = self.start(max_attempts=2)
        event = self.request()
        publish, accepted = self.room.publish, []

        def lose_ack(*args: Any) -> None:
            accepted.append(publish(*args))
            raise TransportError("Accepted response acknowledgement lost")

        with patch.object(self.room, "publish", side_effect=lose_ack):
            self.assertEqual(agent.receive(event)["status"], "held")
        staged = self.store.get("staged")
        self.request("normalize-2")
        view = self.expire()
        execute = Program.execute
        with (
            patch.object(
                Program, "execute", autospec=True, side_effect=execute
            ) as callbacks,
            patch.object(
                self.room, "publish", side_effect=AssertionError("new publication")
            ) as sends,
            patch.object(self.room, "lookup", side_effect=publish) as lookup,
        ):
            result = agent.recover()
            self.assertEqual(
                result,
                {"status": "completed", "producer": "program", "receipt": accepted[0]},
            )
            lookup.assert_called_once_with(staged["envelope"], "agent", self.clock[0])
            callbacks.assert_not_called()
            sends.assert_not_called()
        self.assertEqual(self.store.get("state"), {"processed": 1, "mode": "ascii"})
        self.assertEqual(self.store.get("queue"), [])
        self.assertEqual(
            self.room.view("agent", self.clock[0])["view"]["facts"], view["facts"]
        )

    def test_unknown_unaccepted_publication_stays_staged_when_lookup_is_uncertain(
        self,
    ) -> None:
        agent = self.start(max_attempts=2)
        event = self.request()
        with patch.object(
            self.room,
            "publish",
            side_effect=TransportError("Destination result unknown"),
        ):
            self.assertEqual(agent.receive(event)["status"], "held")
        staged = self.store.get("staged")
        self.expire()
        execute = Program.execute
        with (
            patch.object(
                Program, "execute", autospec=True, side_effect=execute
            ) as callbacks,
            patch.object(
                self.room, "lookup", side_effect=TransportError("Still unknown")
            ) as lookup,
        ):
            self.assertEqual(agent.recover()["status"], "held")
            lookup.assert_called_once_with(staged["envelope"], "agent", self.clock[0])
            callbacks.assert_not_called()
        self.assertEqual(self.store.get("staged")["envelope"], staged["envelope"])
        self.assertEqual(self.store.get("queue"), [event["id"]])
        self.assertEqual(self.store.get("state"), INITIAL)
        self.assertEqual(self.provider.calls, [])

    def test_generic_factory_service_advances_expired_inbox_and_reaches_idle_retention(
        self,
    ) -> None:
        room_store = Store(self.root / "room.sqlite3")
        self.addCleanup(room_store.close)
        room = DurableRoom(
            room_store,
            self.authority,
            session="s",
            bindings={"requester": "user", "normalizer": "agent"},
            clock=lambda: self.clock[0],
        )
        api = RoomAPI(room)
        grant = {"actor": "agent", "permissions": ["room", "observe"]}

        def local_request(
            transport: Any, method: Any, path: Any, data: Any = None
        ) -> Any:
            status, result = api.dispatch(method, path, grant, data)
            self.assertEqual(status, 200)
            return result

        text = CONTRACT.read_text()
        checksum = hashlib.sha256(text.encode()).hexdigest()
        context = {
            "source": {
                "profile": "band-contract/file-source/1",
                "format": "frontmatter",
                "sha256": checksum,
                "content": text,
            },
            "config": {"bindings": {"requester": "user", "normalizer": "agent"}},
            "history": [],
        }
        quality = policy()
        quality["source_sha256"] = checksum
        candidate = {
            "classification": "program",
            "reason": "Bounded ASCII normalization",
            "handles": ["ASCII normalization"],
            "delegates": [],
            "evidence": [],
            "source": SOURCE,
        }
        with patch("remote_agent.band.serve_programs.invoke", return_value=candidate):
            assessment = assessor(self.root / "assessment", quality_policies=[quality])(
                context, {"id": "normalizer", "role": "normalizer"}
            )
        self.assertEqual(assessment["runtime_profile"], "contract-role/2")
        # ProgramHost.assessment adds the requested role to the assessor's verdict.
        assessment = dict(assessment, role="normalizer")
        key = self.root / "agent.key"
        key.write_text("k" * 32)
        key.chmod(0o600)
        value = {
            "room": "room",
            "session": "s",
            "actor": "User:operator",
            "context": context,
            "request": {
                "mode": "program",
                "journal": True,
                "target_bytes": 1,
                "minimum_steps": 1,
            },
        }
        host = {
            "agent_keys": {"agent": str(key)},
            "engine_command": self.command,
            "model_config": str(self.root / "unused-model.json"),
            "platform_url": "https://fixture.invalid",
        }
        with (
            patch("remote_agent.network.HTTPRoom._request", new=local_request),
            patch("remote_agent.service.CommandProvider", return_value=self.provider),
            patch("remote_agent.service.HTTPService", NoListener),
            patch("remote_agent.band.generic.model_arguments", return_value=[]),
            patch.object(GenericRuntimeDriver, "run", lambda driver: None),
        ):
            driver = create_runtime(
                value, assessment, self.root / "runtime", host_config=host
            )
            try:
                self.assertIsInstance(driver, GenericRuntimeDriver)
                event = room.publish(
                    {
                        "id": "normalize-1",
                        "session": "s",
                        "kind": "normalize",
                        "payload": {"text": "Band"},
                        "expected_version": 0,
                    },
                    "user",
                )
                self.clock[0] = 120000
                terminal = room.view("agent")["view"]
                self.assertTrue(terminal["obligations"])
                self.assertEqual(
                    (terminal["status"], terminal["legal"]), ("terminal", [])
                )
                results = []
                agent = require_value(
                    driver.service.agent, "The runtime service must have an agent"
                )
                retain = agent.retain_history

                def retaining(**options: Any) -> Any:
                    result = retain(**options)
                    results.append(result)
                    return result

                execute = Program.execute
                with (
                    patch.object(
                        Program, "execute", autospec=True, side_effect=execute
                    ) as callbacks,
                    patch.object(
                        driver.service.agent, "retain_history", side_effect=retaining
                    ),
                    patch.object(
                        driver.stopping,
                        "wait",
                        side_effect=lambda seconds: driver.stopping.set(),
                    ),
                ):
                    RuntimeDriver.run(driver)
                    agent.wait_for_repair(2)
                    self.assertFalse(driver.service.held)
                    self.assertEqual(
                        driver.service.store.get("input:" + event["id"])["result"], DONE
                    )
                    self.assertEqual(driver.service.store.get("queue"), [])
                    self.assertEqual(driver.service.store.get("state"), INITIAL)
                    self.assertEqual(driver.service.store.get("model_calls"), 0)
                    self.assertEqual(self.provider.calls, [])
                    self.assertEqual(
                        driver.service.store.get("inbox_cursor"),
                        room_store.journal_head()["sequence"],
                    )
                    self.assertEqual(driver.last_retained_turns, 1)
                    self.assertEqual(len(results), 1)
                    self.assertEqual(results[0]["retained_steps"], 1)
                    callbacks.assert_not_called()
            finally:
                driver.close()
