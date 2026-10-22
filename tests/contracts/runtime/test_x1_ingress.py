from __future__ import annotations

import http.client
import json
import socket
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest

from remote_agent.controller import HybridAgent
from remote_agent.endpoint import Endpoint
from remote_agent.interfaces import HTTPBackend
from remote_agent.network import HTTPService


@pytest.mark.contract_posix
class TestIngressSetupFailure(unittest.TestCase):
    def test_failed_descriptor_or_timer_setup_releases_slot_socket_and_metrics(
        self,
    ) -> None:
        for ingress in ("endpoint", "service"):
            for fault in ("dup", "timer"):
                with self.subTest(ingress=ingress, fault=fault):
                    if ingress == "endpoint":
                        instance = Endpoint(
                            cast(
                                HybridAgent,
                                SimpleNamespace(
                                    receive=lambda _: {"status": "completed"}
                                ),
                            ),
                            {"r" * 32: {"actor": "room", "events": True}},
                            max_connections=1,
                            notify=lambda _: {"status": "notified"},
                        )
                    else:
                        instance = HTTPService(
                            cast(HTTPBackend, SimpleNamespace()),
                            {"r" * 32: {"actor": "room", "permissions": ["events"]}},
                            max_connections=1,
                        )
                    self.addCleanup(instance.close)
                    server = instance.server
                    request, peer = socket.socketpair()
                    self.addCleanup(request.close)
                    self.addCleanup(peer.close)
                    duplicated = []

                    def fail_timer(
                        timer: Any, *, duplicated: list[socket.socket] = duplicated
                    ) -> None:
                        guard = timer.function.__self__
                        duplicated.append(guard.socket)
                        self.addCleanup(
                            guard.close
                        )  # Clean baseline leak after recording failure.
                        raise RuntimeError("injected thread limit")

                    self.assertTrue(server.slots.acquire(blocking=False))
                    injection = (
                        patch(
                            "remote_agent.network.os.dup",
                            side_effect=OSError("injected EMFILE"),
                        )
                        if fault == "dup"
                        else patch(
                            "remote_agent.network.threading.Timer.start", fail_timer
                        )
                    )
                    with injection:
                        try:
                            server.process_request_thread(request, ("local", 0))
                        except (OSError, RuntimeError):
                            pass
                    self.assertEqual(
                        request.fileno(),
                        -1,
                        "failed setup must close the accepted socket",
                    )
                    self.assertTrue(
                        all(sock.fileno() == -1 for sock in duplicated),
                        "timer failure leaked the dup socket",
                    )
                    self.assertTrue(
                        server.slots.acquire(blocking=False),
                        "failed setup consumed the only slot",
                    )
                    server.slots.release()
                    if ingress == "service":
                        assert isinstance(instance, HTTPService)
                        self.assertEqual(
                            instance.server.metrics()["active_connections"], 0
                        )
                    # Verify a real subsequent request succeeds, not just the semaphore count.
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", server.server_address[1], timeout=2
                    )
                    try:
                        if ingress == "service":
                            connection.request("GET", "/health/live")
                        else:
                            connection.request(
                                "POST",
                                "/events",
                                b"{}",
                                headers={
                                    "Authorization": "Bearer " + "r" * 32,
                                    "Content-Type": "application/json",
                                },
                            )
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        json.loads(response.read())
                    finally:
                        connection.close()
