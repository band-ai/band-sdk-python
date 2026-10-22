from __future__ import annotations

import hmac
import http.client
import json
import unittest
from types import SimpleNamespace
from typing import cast

import pytest

from remote_agent.controller import HybridAgent
from remote_agent.endpoint import Endpoint
from remote_agent.interfaces import HTTPBackend
from remote_agent.network import HTTPService


@pytest.mark.contract_posix
class TestUnequalBearer(unittest.TestCase):
    def test_unequal_lengths_return_401_without_dispatch_on_both_ingresses(
        self,
    ) -> None:
        self.assertFalse(hmac.compare_digest(b"a" * 32, b"a" * 40))
        calls = []
        agent = SimpleNamespace(receive=lambda data: calls.append(data))
        backend = SimpleNamespace(dispatch=lambda *args: calls.append(args))
        endpoint = Endpoint(
            cast(HybridAgent, agent), {"a" * 32: {"actor": "room", "events": True}}
        )
        service = HTTPService(
            cast(HTTPBackend, backend),
            {"a" * 32: {"actor": "room", "permissions": ["events"]}},
        )
        self.addCleanup(endpoint.close)
        self.addCleanup(service.close)
        for server, path in ((endpoint, "/events"), (service, "/v1/events")):
            for length in (1, 31, 33, 40, 1024):
                with self.subTest(ingress=path, length=length):
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", server.server.server_address[1], timeout=2
                    )
                    try:
                        connection.request(
                            "POST",
                            path,
                            b"{}",
                            headers={
                                "Authorization": "Bearer " + "a" * length,
                                "Content-Type": "application/json",
                            },
                        )
                        response = connection.getresponse()
                        self.assertEqual(response.status, 401)
                        self.assertEqual(
                            json.loads(response.read()),
                            {"error": "Authentication failed"},
                        )
                    finally:
                        connection.close()
        self.assertEqual(calls, [])
