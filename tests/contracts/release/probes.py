"""Provide observable HTTP peers for the release qualification probes."""

from __future__ import annotations

import copy
import threading
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class ProbePeer:
    """Record requests and expose the identity returned by a create retry."""

    def __init__(self) -> None:
        self.data = {
            "room": "r",
            "other_room": "other",
            "session": "s",
            "requester_key": "requester",
            "observer_key": "observer",
            "outsider_key": "outsider",
            "observer_agent_key": "observer-agent",
            "outsider_agent_key": "outsider-agent",
        }
        self.request = {
            "id": "s",
            "source": {"content": "source"},
            "config": {"max_messages": 100},
        }
        self.calls: list[tuple[int, str, str, dict[str, Any] | None, int]] = []
        self.retry_identity = "same"

    def http(
        self,
        port: int,
        path: str,
        key: str,
        body: dict[str, Any] | None = None,
        expected: int = 200,
        method: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append((port, path, key, copy.deepcopy(body), expected))
        match expected:
            case 404:
                return {"error": "not_found"}
            case 409:
                return {"error": "id_conflict"}
            case 201:
                return {"identity": self.retry_identity}
            case _:
                return {"identity": "same"}


class PermissiveHost(BaseHTTPRequestHandler):
    """Return a successful response for a request that the probe must reject."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"identity":"private"}')

    def log_message(self, format: str, *args: Any) -> None:
        return


@contextmanager
def permissive_host() -> Generator[int, None, None]:
    """Own the real server and await its worker's shutdown."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), PermissiveHost)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
