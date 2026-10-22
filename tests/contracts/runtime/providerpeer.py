"""Return one model result only after the test releases its process gate."""

from __future__ import annotations

import argparse
import json
import socket
import sys


def read_token(connection: socket.socket, expected: bytes) -> None:
    received = bytearray()
    while len(received) < len(expected):
        chunk = connection.recv(len(expected) - len(received))
        if not chunk:
            raise AssertionError(
                "The process gate closed before confirming its boundary"
            )
        received.extend(chunk)
    if received != expected:
        raise AssertionError("The process gate confirmed a different boundary")


def reach_gate(port: int, token: str, *, wait_for_release: bool) -> None:
    expected = token.encode("ascii")
    with socket.create_connection(("127.0.0.1", port), timeout=8) as connection:
        connection.sendall(expected)
        if wait_for_release:
            read_token(connection, expected)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("port", type=int)
    parser.add_argument("token")
    args = parser.parse_args()
    request = json.load(sys.stdin)
    reach_gate(args.port, args.token, wait_for_release=True)
    result = (
        {
            "state": {"count": 1},
            "message": {
                "kind": "answer",
                "payload": {
                    "request": request["bundle"]["input"]["id"],
                    "text": "model answer",
                },
            },
            "scope": "turn",
        }
        if "bundle" in request
        else {"source": "structured result"}
    )
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
