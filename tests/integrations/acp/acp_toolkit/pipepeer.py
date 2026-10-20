"""Control messages and a descendant that holds its agent's inherited pipes."""

from __future__ import annotations

import os
import socket
import sys
from enum import StrEnum

CONTROL_PORT_ARG = "--control-port"
CONTROL_HOST = "127.0.0.1"


class PipeRole(StrEnum):
    AGENT = "agent"
    HOLDER = "holder"


class PipeCommand(StrEnum):
    START = "start"
    EXIT = "exit"
    RELEASE = "release"


def control_line(value: PipeRole | PipeCommand) -> bytes:
    return f"{value}\n".encode()


def hold_pipes(port: int) -> None:
    with socket.create_connection((CONTROL_HOST, port)) as connection:
        connection.sendall(control_line(PipeRole.HOLDER))
        with connection.makefile("rb") as commands:
            command = commands.readline()
            if command not in (b"", control_line(PipeCommand.RELEASE)):
                raise ValueError(f"Unexpected holder command: {command!r}")
    os._exit(0)


if __name__ == "__main__":
    hold_pipes(int(sys.argv[1]))
