from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

from remote_agent.authority import DurableRoom
from remote_agent.controller import HybridAgent
from remote_agent.network import AgentAPI, HTTPRoom, HTTPService, RoomAPI
from remote_agent.service import Service
from remote_agent.store import Store
from remote_agent.transport import EngineProcess, TransportError


class InboxHoldFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.path = root / "agent.sqlite3"
        self.store = Store(self.path)
        self.addCleanup(lambda: self.store.close())
        self.store.commit(
            "fixture",
            {},
            updates={
                "inbox_cursor": 5,
                "queue": [],
                "staged": None,
                "execution": None,
                "mode": "program",
                "config": {"session": "s", "actor": "agent"},
            },
        )
        self.room_store = Store(root / "room.sqlite3")
        self.addCleanup(self.room_store.close)
        self.room_store.commit("room_started", {})
        self.engine = SimpleNamespace(
            closed=False, process=SimpleNamespace(poll=lambda: None)
        )
        self.room = SimpleNamespace(
            store=self.room_store,
            failed=False,
            lock=threading.RLock(),
            engine=self.engine,
            session="s",
            identity={},
            config={"bindings": {"receiver": "agent"}},
        )
        self.http = HTTPService(
            RoomAPI(cast(DurableRoom, self.room)),
            {"a" * 32: {"actor": "agent", "permissions": ["room"]}},
        )
        self.addCleanup(self.http.close)
        self.client = HTTPRoom(self.http.url, "a" * 32, require_inbox=True)
        self.service = self.restart_service()
        self.grant = {"actor": "operator", "permissions": ["controls"]}

    def restart_service(self) -> Any:
        agent = HybridAgent.__new__(HybridAgent)
        agent.store, agent.room, agent.actor = self.store, self.client, "agent"
        agent.lock, agent.stopping, agent.closed = (
            threading.RLock(),
            threading.Event(),
            False,
        )
        agent.control_actors = {
            "operator": ["retry", "resume", "reconcile"],
            "observer": ["retry"],
        }
        agent.now = lambda: 0
        agent.limits = {"max_attempts": 3}
        agent.recover = Mock(return_value={"status": "idle"})
        agent.receive = Mock(return_value={"status": "completed"})
        agent._kick_repair = Mock()
        service = Service.__new__(Service)
        service.agent, service.room, service.store, service.engine = (
            agent,
            self.client,
            self.store,
            cast(EngineProcess, self.engine),
        )
        service.stopping, service.poll_wake = threading.Event(), threading.Event()
        service.held, service.last_fault, service.poll_failures = False, None, 0
        service.backend = AgentAPI(agent, notify=service.notify)
        return service

    def observe_conflict(self) -> None:
        # The durable hold is the observable result even if conflict handling
        # also reports a retryable transport error.
        try:
            self.service.cycle()
        except TransportError:
            pass

    def grow_room(self) -> Any:
        event = {"status": "accepted", "id": "new-fact", "recipients": ["agent"]}
        self.room_store.commit("room_operation", {"result": event})
        for _ in range(5):
            self.room_store.commit("room_started", {})
        return event

    def control(
        self,
        identifier: str = "reconcile-1",
        action: str = "reconcile",
        actor: str = "operator",
    ) -> Any:
        return self.service.dispatch(
            "POST",
            "/v1/controls",
            dict(self.grant, actor=actor),
            {
                "id": identifier,
                "action": action,
                "reason": "Operator verified the restored history and pending effects",
            },
        )
