from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from typing import Any, cast

from remote_agent.controller import HybridAgent
from remote_agent.network import HTTPRoom
from remote_agent.service import Service
from remote_agent.transport import EngineProcess
from tests.contracts.runtime.setql9 import HandoffLock, LockOwnership


class TestCatchUpLock(unittest.TestCase):
    def test_waiting_control_runs_between_pages_and_page_processing_stays_locked(
        self,
    ) -> None:
        service = Service.__new__(Service)
        service.engine = cast(
            EngineProcess,
            SimpleNamespace(closed=False, process=SimpleNamespace(poll=lambda: None)),
        )
        service.room = cast(HTTPRoom, SimpleNamespace())
        service.stopping = threading.Event()
        first_page = threading.Event()
        control_done = threading.Event()
        lock = HandoffLock(control_done)
        service.agent = cast(HybridAgent, SimpleNamespace(lock=lock))
        observed = []

        def control() -> None:
            if first_page.wait(2):
                with lock.lock:
                    control_done.set()

        worker = threading.Thread(target=control)
        worker.start()
        self.addCleanup(worker.join, 2)

        def page() -> Any:
            self.assertTrue(
                cast(LockOwnership, lock.lock)._is_owned(),
                "Page must retain poll/commit serialization",
            )
            observed.append(control_done.is_set())
            first_page.set()
            return len(observed) < 2

        service._agent_page = page
        service.cycle()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(
            observed, [False, True], "Whole-cycle locking starves the waiting control"
        )
        self.assertEqual(lock.exits, 2)

    def test_stop_between_pages_prevents_another_fetch(self) -> None:
        service = Service.__new__(Service)
        service.engine = cast(
            EngineProcess,
            SimpleNamespace(closed=False, process=SimpleNamespace(poll=lambda: None)),
        )
        service.room = cast(HTTPRoom, SimpleNamespace())
        service.stopping = threading.Event()
        service.agent = cast(HybridAgent, SimpleNamespace(lock=threading.RLock()))
        pages = []

        def page() -> bool:
            pages.append(1)
            service.stopping.set()
            return True

        service._agent_page = page
        service.cycle()
        self.assertEqual(pages, [1])
