from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from queue import Empty
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from remote_agent.controller import ControlError
from remote_agent.program import Program
from remote_agent.transport import TransportError
from tests.contracts.runtime.genericterminalretry import (
    DONE,
    INITIAL,
    GenericTerminalRetryFixture,
    RunningHistoryProvider,
)
from tests.contracts.runtime.required import require_value
from tests.contracts.runtime.timers import DeadlineQueue, TimerClock


@pytest.mark.contract_isolation
@pytest.mark.contract_engine("band")
class TestGenericTerminalRetry(GenericTerminalRetryFixture):
    def test_default_exhausted_history_explicit_retry_finishes_terminal_generic_runtime(
        self,
    ) -> None:
        self.fixture()
        self.exhaust()
        before, pins = self.pending(), self.pins()
        terminal = self.expire()
        self.assertTrue(
            terminal["obligations"], "Ordinary expired duties remain diagnostic"
        )
        with self.assertRaises(ControlError) as denied:
            self.retry("denied", actor="User:outsider")
        self.assertEqual(denied.exception.status, 403)
        self.assertEqual(self.pending(), before)
        with patch.object(
            self.driver.service.room,
            "view",
            side_effect=TransportError("Authority unavailable"),
        ):
            refused = self.retry("no-authority")
        self.assertEqual(refused["status"], "held")
        self.assertEqual(self.pending(), before)
        self.assertEqual(
            self.retry("no-authority"), refused, "A failed control ID stays idempotent"
        )
        with self.assertRaises(ControlError) as conflict:
            self.retry("no-authority", reason="Conflicting same ID")
        self.assertEqual(conflict.exception.status, 409)
        execute = Program.execute
        with (
            patch.object(
                Program, "execute", autospec=True, side_effect=execute
            ) as callbacks,
            patch.object(
                self.driver.service.room,
                "publish",
                wraps=self.driver.service.room.publish,
            ) as publish,
            patch.object(
                self.driver.service.room,
                "lookup",
                wraps=self.driver.service.room.lookup,
            ) as lookup,
        ):
            result = self.retry("terminal-retry")
            self.assertEqual(result, DONE)
            self.assertEqual(self.store.get("queue"), [])
            self.assertIsNone(self.store.get("execution"))
            self.assertIsNone(self.store.get("staged"))
            self.assertEqual(self.store.get("state"), INITIAL)
            self.assertEqual(
                require_value(
                    self.store.latest_snapshot(), "Expected a committed snapshot"
                )["state"],
                INITIAL,
            )
            self.assertEqual(self.store.get("attempts:" + self.event["id"]), 3)
            self.assertEqual(self.store.get("model_calls"), 4)
            self.assertEqual(self.provider.calls, [0, 1, 1, 1])
            for key, value in pins.items():
                self.assertEqual(self.store.get(key), value, key)
            self.driver.service.cycle()
            self.assertFalse(self.driver.service.held)
            self.assertEqual(
                self.store.get("inbox_cursor"),
                self.room_store.journal_head()["sequence"],
            )
            self.assertEqual(self.store.get("turns"), 1)
            self.assertEqual(self.retry("terminal-retry"), result)
            self.assertEqual(self.agent.receive(self.event), result)
            self.assertEqual(self.agent.recover(), {"status": "idle"})
            self.assertEqual(
                self.room.view("agent")["view"]["facts"], terminal["facts"]
            )
            callbacks.assert_not_called()
            publish.assert_not_called()
            lookup.assert_not_called()
        self.store._verify()

    def test_live_exhausted_history_stays_held_without_spending_more_calls(
        self,
    ) -> None:
        self.fixture()
        self.exhaust()
        before, pins = self.pending(), self.pins()
        result = self.retry("live-exhausted")
        self.assertEqual(result["status"], "held")
        self.assertIn("attempt budget", result["reason"])
        self.driver.service.cycle()
        self.assertTrue(self.driver.service.held)
        self.assertEqual(self.room.view("agent")["view"]["status"], "active")
        self.assertEqual(self.pending(), before)
        self.assertEqual(self.provider.calls, [0, 1, 1, 1])
        for key, value in pins.items():
            self.assertEqual(self.store.get(key), value)
        self.store._verify()

    def test_terminal_retry_still_refuses_a_real_running_callback(self) -> None:
        provider = RunningHistoryProvider()
        self.fixture(provider=provider)
        self.agent.model_timeout = 10
        self.addCleanup(provider.release.set)
        clock = TimerClock()
        queues = SimpleNamespace(
            Queue=lambda maxsize: DeadlineQueue(clock, maxsize), Empty=Empty
        )
        with (
            patch("remote_agent.controller.queue", queues),
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            pending = executor.submit(self.driver.service.cycle)
            self.assertTrue(provider.started.wait(5))
            clock.await_timer(2)
            clock.advance(9)
            self.assertFalse(pending.done())
            clock.advance(1)
            pending.result(timeout=5)
        self.assertTrue(provider.started.is_set())
        self.assertEqual(provider.calls, [0, 1])
        before, pins = self.pending(), self.pins()
        job = self.agent.jobs["respond"]
        self.assertTrue(job.is_alive())
        terminal = self.expire()
        try:
            result = self.retry("callback-running")
            self.assertEqual(result["status"], "held")
            self.assertIn("still running", result["reason"])
            self.assertEqual(self.pending(), before)
        finally:
            provider.release.set()
            job.join(2)
        self.assertFalse(job.is_alive())
        self.assertEqual(self.retry("callback-running"), result)
        self.assertEqual(self.pending(), before)
        self.assertEqual(self.retry("callback-stopped"), DONE)
        self.assertEqual(self.store.get("attempts:" + self.event["id"]), 1)
        self.assertEqual(provider.calls, [0, 1])
        self.assertEqual(self.store.get("model_calls"), 2)
        self.assertEqual(self.store.get("state"), INITIAL)
        self.assertEqual(self.room.view("agent")["view"]["facts"], terminal["facts"])
        for key, value in pins.items():
            self.assertEqual(self.store.get(key), value)
        self.store._verify()

    def test_terminal_unknown_staged_effect_keeps_the_exact_proposal(self) -> None:
        self.assert_staged_reconciliation(accepted=False)

    def test_terminal_accepted_staged_effect_commits_its_state_once(self) -> None:
        self.assert_staged_reconciliation(accepted=True)
