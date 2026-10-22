"""Own a loopback HTTP peer through startup and confirmed shutdown."""

from __future__ import annotations

import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class HTTPPeer:
    def __init__(self, handler: Callable[..., BaseHTTPRequestHandler]) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.closed = False

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        if self.closed:
            return
        try:
            if self.thread.ident is not None:
                self.server.shutdown()
            self.server.server_close()
            if self.thread.ident is not None:
                self.thread.join(timeout=5)
                if self.thread.is_alive():
                    raise AssertionError("The HTTP peer did not stop")
        finally:
            self.closed = True
