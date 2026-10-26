"""A local HTTP server standing in for the Slack and Discord webhooks."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass(frozen=True)
class Received:
    method: str
    path: str
    body: bytes
    user_agent: str


@dataclass(frozen=True)
class Reply:
    status: int
    body: bytes


UNSCRIPTED = Reply(500, b"unscripted request")
WEBHOOK_PATH = "/api/webhooks/1/token"
# How often serve_forever checks for shutdown; bounds each test's teardown.
SHUTDOWN_POLL_S = 0.01


@dataclass
class WebhookPeer:
    """Answers each request with the next queued reply and records it.

    A request with no queued reply gets ``UNSCRIPTED``, so a test never passes
    on a response it did not script.
    """

    url: str
    replies: list[Reply] = field(default_factory=list)
    received: list[Received] = field(default_factory=list)

    def reply(self, status: int, body: bytes = b"") -> None:
        self.replies.append(Reply(status, body))

    def answer(self, request: Received) -> Reply:
        self.received.append(request)
        return self.replies.pop(0) if self.replies else UNSCRIPTED


class PeerHandler(BaseHTTPRequestHandler):
    server: PeerServer

    def answer(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        request = Received(
            self.command,
            self.path,
            self.rfile.read(length),
            self.headers.get("User-Agent", ""),
        )
        reply = self.server.peer.answer(request)
        self.send_response(reply.status)
        self.send_header("Content-Length", str(len(reply.body)))
        self.end_headers()
        self.wfile.write(reply.body)

    do_POST = do_DELETE = answer

    def log_message(self, message_format: str, *args: object) -> None:
        """Keep test output quiet."""


class PeerServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), PeerHandler)
        host, port = self.server_address[:2]
        self.peer = WebhookPeer(url=f"http://{host!s}:{port}{WEBHOOK_PATH}")


@contextmanager
def webhook_peer() -> Iterator[WebhookPeer]:
    """Serve a peer on an ephemeral port until the block exits."""
    with PeerServer() as server:
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": SHUTDOWN_POLL_S}
        )
        thread.start()
        try:
            yield server.peer
        finally:
            server.shutdown()
            thread.join()
