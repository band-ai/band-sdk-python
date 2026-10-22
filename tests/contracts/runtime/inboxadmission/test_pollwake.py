from __future__ import annotations

import threading
from unittest.mock import patch

from tests.contracts.runtime.inboxadmission.kit import PollWakeFixture


class TestPollWake(PollWakeFixture):
    def test_wake_during_cycle_is_not_lost_and_is_coalesced(self) -> None:
        service = self.service()
        calls = []

        def cycle() -> None:
            calls.append(1)
            if len(calls) == 1:
                service.wake()
                service.wake()
            else:
                service.request_stop()

        service.cycle = cycle
        thread = threading.Thread(target=service.run)
        thread.start()
        self.addCleanup(self.stop_thread, service, thread)
        thread.join(2)
        self.assertFalse(thread.is_alive(), "wake arriving during cycle was lost")
        self.assertEqual(len(calls), 2)

    def test_shutdown_interrupts_the_poll_wait(self) -> None:
        service = self.service()
        cycled = threading.Event()
        service.cycle = cycled.set
        thread = threading.Thread(target=service.run)
        thread.start()
        self.addCleanup(self.stop_thread, service, thread)
        self.assertTrue(service.poll_wake.wait_started.wait(5))
        self.assertTrue(cycled.is_set())
        self.assertTrue(thread.is_alive())
        service.request_stop()
        thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_shutdown_racing_wake_clear_does_not_wait_a_full_interval(self) -> None:
        service = self.service()
        service.cycle = lambda: None
        clear = service.poll_wake.clear

        def stop_before_clear() -> None:
            service.request_stop()
            clear()

        with patch.object(service.poll_wake, "clear", side_effect=stop_before_clear):
            thread = threading.Thread(target=service.run)
            thread.start()
            self.addCleanup(self.stop_thread, service, thread)
            thread.join(2)
            self.assertFalse(thread.is_alive(), "shutdown signal erased by wake clear")
