from __future__ import annotations

import http.client
import json
import unittest
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest.mock import Mock

from remote_agent.controller import HybridAgent
from remote_agent.endpoint import Endpoint
from remote_agent.network import AgentAPI, HTTPService


class NotifyOnlyIngressFixture(unittest.TestCase):
    event: ClassVar[dict[str, Any]] = {
        "id": "q2",
        "session": "s",
        "kind": "question",
        "payload": {"text": "ping"},
        "actor": "user",
        "role": "requester",
    }

    def make(
        self, ingress: Any, notify: Any, configured: bool = True
    ) -> tuple[Any, ...]:
        agent = SimpleNamespace(
            closed=False,
            store=SimpleNamespace(db=object()),
            receive=Mock(return_value={"status": "completed"}),
            recover=Mock(),
            control=Mock(return_value={"status": "completed"}),
        )
        callback = notify if configured else None
        if ingress == "endpoint":
            server = Endpoint(
                cast(HybridAgent, agent),
                {"r" * 32: {"actor": "room", "events": True}},
                notify=callback,
            )
            path = "/events"
        else:
            server = HTTPService(
                AgentAPI(cast(HybridAgent, agent), notify=callback),
                {"r" * 32: {"actor": "room", "permissions": ["events"]}},
            )
            path = "/v1/events"
        self.addCleanup(server.close)
        return agent, server, path

    def post(self, server: Any, path: Any, event: Any) -> tuple[Any, ...]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server.server_address[1], timeout=2
        )
        try:
            connection.request(
                "POST",
                path,
                json.dumps(event).encode(),
                headers={
                    "Authorization": "Bearer " + "r" * 32,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
