"""Confirm a subprocess boundary and release only that owned process."""

from __future__ import annotations

import socket
import sys
from uuid import uuid4

from tests.contracts.runtime.providerpeer import read_token
from tests.paths import CONTRACT_RUNTIME_TESTS


class ProcessGate:
    def __init__(self) -> None:
        self.listener = socket.create_server(("127.0.0.1", 0), backlog=1)
        self.listener.settimeout(8)
        self.port = self.listener.getsockname()[1]
        self.token = uuid4().hex
        self.connection: socket.socket | None = None

    def command(self) -> list[str]:
        return [
            sys.executable,
            str(CONTRACT_RUNTIME_TESTS / "providerpeer.py"),
            str(self.port),
            self.token,
        ]

    def await_entry(self) -> None:
        self.connection, _ = self.listener.accept()
        self.connection.settimeout(8)
        read_token(self.connection, self.token.encode("ascii"))

    def release(self) -> None:
        if self.connection is None:
            raise AssertionError("The process has not reached its release gate")
        self.connection.sendall(self.token.encode("ascii"))
        self.connection.close()
        self.connection = None

    def await_closed(self) -> None:
        if self.connection is None:
            raise AssertionError("The process has not reached its release gate")
        if self.connection.recv(1) != b"":
            raise AssertionError("The expired process produced data after its deadline")

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self.listener.close()
