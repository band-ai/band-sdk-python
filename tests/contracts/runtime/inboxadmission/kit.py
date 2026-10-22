from __future__ import annotations

import http.client
import json
import threading
import unittest
from typing import Any

import tests.contracts.runtime.service as service_tests
from remote_agent.service import Service, load_config

CONTRACT = """---
name: ordered-notes
description: Accept independent notes in room order.
engine: 0.2.0-lab.1
roles: [requester, assistant]
---
contract @infinity => expired
type note
  text: text(1..100)
state new
  event requester -> assistant: note()
"""


PROGRAM = """from remote_agent.sdk import Handled
STATE_VERSION = "1"
COVERAGE = {"mode": "full", "handles": ["notes"], "delegates": []}
def initial(view):
    return {"order": []}
def handle(event, state, context):
    state["order"].append(event["id"])
    return Handled(state)
"""


class InboxAdmissionFixture(service_tests.ServiceFixture):
    def services(self) -> tuple[Any, ...]:
        self.contract.write_text(CONTRACT)
        self.engine["format"] = "frontmatter"
        room_config = load_config(self.private("room-config.json", self.room_config()))
        room = Service(room_config)
        self.addCleanup(room.close)
        config = self.agent_config(room.http.url)
        self.private("program.py", PROGRAM)
        self.private(
            "agent-tokens.json",
            {"o" * 32: {"actor": "room", "permissions": ["events"]}},
        )
        config = load_config(self.private("agent-config.json", config))
        agent = Service(config)
        self.addCleanup(agent.close)
        return room, agent, config

    def push(self, service: Any, event: Any) -> tuple[Any, ...]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", service.http.server.server_address[1], timeout=5
        )
        try:
            connection.request(
                "POST",
                "/v1/events",
                json.dumps(event),
                {
                    "Authorization": "Bearer " + "o" * 32,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def note(self, client: Any, identifier: Any, version: Any) -> Any:
        event = client.publish(
            {
                "id": identifier,
                "session": "s",
                "kind": "note",
                "payload": {"text": identifier},
                "expected_version": version,
            },
            "user",
        )
        self.assertEqual(event["status"], "accepted")
        return event


class PollWakeFixture(unittest.TestCase):
    def service(self) -> Any:
        service = Service.__new__(Service)
        service.config = {"poll_interval_ms": 60000}
        service.stopping = threading.Event()
        service.poll_wake = ObservedWake()
        service.agent = None
        service.last_fault = None
        service.poll_failures = 0
        return service

    def stop_thread(self, service: Service, thread: threading.Thread) -> None:
        service.request_stop()
        service.poll_wake.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "The owned polling thread did not stop")


class ObservedWake(threading.Event):
    def __init__(self) -> None:
        super().__init__()
        self.wait_started = threading.Event()

    def wait(self, timeout: float | None = None) -> bool:
        self.wait_started.set()
        return super().wait(timeout)
