from __future__ import annotations

import json
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from remote_agent.controller import HybridAgent
from remote_agent.endpoint import Endpoint
from remote_agent.program import Artifact, Program
from remote_agent.store import Store
from remote_agent.transport import EngineProcess, LocalGate, ReferenceRoom
from tests.contracts.runtime.hybrid import ScriptedModel
from tests.contracts.runtime.program import SOURCE
from tests.contracts.runtime.timers import TimerClock, await_capacity
from tests.paths import CONTRACTS_ROOT


class EndpointFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        contract = CONTRACTS_ROOT / "contract-lab/contracts/hybrid-assistant.band"
        cls.authority, cls.checker = EngineProcess(contract), EngineProcess(contract)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.authority.close()
        cls.checker.close()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hybrid-endpoint-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.store = Store(self.path / "agent.sqlite3")
        self.addCleanup(self.store.close)
        self.room = ReferenceRoom(
            self.authority,
            session="s",
            bindings={"requester": "user", "assistant": "agent"},
        )
        self.provider = ScriptedModel()
        self.artifact = Artifact.publish(SOURCE, self.path / "artifacts")
        self.options = {
            "actor": "agent",
            "artifact_directory": self.path / "artifacts",
            "now": lambda: 0,
            "control_actors": {
                "user": ["wake", "takeover", "resume", "retry", "repair"],
                "observer": ["wake"],
            },
            "validator": self.validate_candidate,
        }
        self.agent = self.make_agent()
        self.notifications = []
        self.endpoint = Endpoint(
            self.agent,
            {
                "r" * 32: {"actor": "room", "events": True},
                "u" * 32: {"actor": "user", "events": False},
                "o" * 32: {"actor": "observer", "events": False},
            },
            notify=self.notify,
        )
        self.addCleanup(self.endpoint.close)

    @staticmethod
    def validate_candidate(
        candidate: Program, bundle: dict[str, Any]
    ) -> dict[str, Any]:
        return {"ok": True, "checks": ["fixture answer semantics"]}

    def notify(self, event: Any) -> dict[str, Any]:
        self.room.accepted_input(event, "agent", 0)
        self.notifications.append(event["id"])
        return {"status": "notified"}

    def make_agent(self) -> Any:
        agent = HybridAgent(
            self.store,
            Program(self.artifact),
            self.room,
            LocalGate(self.checker),
            self.provider,
            **self.options,
        )
        self.addCleanup(agent.close)
        return agent

    def post(self, path: Any, data: Any, token: Any = "u" * 32) -> tuple[Any, ...]:
        request = urllib.request.Request(
            self.endpoint.url + path,
            data=json.dumps(data).encode(),
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def question(self) -> Any:
        version = self.room.view("user", 0)["view"]["version"]
        return self.room.publish(
            {
                "id": "q" + str(version),
                "session": "s",
                "kind": "question",
                "payload": {"text": "ping"},
                "expected_version": version,
            },
            "user",
            0,
        )


class EndpointIngressFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.received = []
        self.clock = TimerClock()
        timer_patch = patch("remote_agent.network.threading.Timer", self.clock.timer)
        timer_patch.start()
        self.addCleanup(timer_patch.stop)
        self.callback_started = threading.Event()
        self.release_callback = threading.Event()
        self.endpoint = Endpoint(
            cast(HybridAgent, SimpleNamespace()),
            {"r" * 32: {"actor": "room", "events": True}},
            read_timeout=10,
            max_connections=1,
            notify=self.notify,
        )
        self.addCleanup(self.endpoint.close)
        self.addCleanup(self.release_callback.set)

    def notify(self, data: dict[str, Any]) -> dict[str, Any]:
        self.received.append(data)
        if data.get("slow"):
            self.callback_started.set()
            if not self.release_callback.wait(5):
                raise AssertionError("The admitted callback was not released")
        return {"status": "completed"}

    def post(self, data: Any) -> Any:
        request = urllib.request.Request(
            self.endpoint.url + "/events",
            data=json.dumps(data).encode(),
            headers={
                "Authorization": "Bearer " + "r" * 32,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            return json.loads(response.read())

    def assert_slow_ingress_releases_capacity(self, prefix: bytes) -> None:
        with socket.create_connection(
            ("127.0.0.1", self.endpoint.server.server_port), timeout=1
        ) as slow:
            slow.sendall(prefix)
            deadline = self.clock.await_timer()
            self.assertFalse(self.endpoint.server.slots.acquire(blocking=False))
            slow.sendall(b" ")
            self.clock.advance(9)
            self.assertFalse(deadline.fired)
            slow.sendall(b" ")
            self.clock.advance(1)
            self.assertTrue(deadline.fired)
            await_capacity(self.endpoint.server.slots)
            self.assertEqual(self.post({"valid": True}), {"status": "completed"})
        self.assertEqual(self.received, [{"valid": True}])
