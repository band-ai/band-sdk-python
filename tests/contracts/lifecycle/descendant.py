"""Owned real-process fixture: one leader leaves a descendant holding its pipes."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys


def main() -> None:
    role, port = sys.argv[1:]
    with socket.create_connection(("127.0.0.1", int(port)), timeout=10) as control:
        control.settimeout(None)
        control.sendall(
            (json.dumps({"role": role, "pid": os.getpid()}) + "\n").encode()
        )
        if role == "leader":
            child = subprocess.Popen(
                [sys.executable, "-I", "-u", __file__, "descendant", port],
                stdin=subprocess.DEVNULL,
            )
            sys.stdout.write(
                json.dumps({"event": "ready", "descendant": child.pid}) + "\n"
            )
            sys.stdout.flush()
            control.recv(1)
            os._exit(0)
        control.recv(1)


if __name__ == "__main__":
    main()
