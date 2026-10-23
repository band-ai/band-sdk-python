"""Keep real descendant processes alive behind an explicitly controlled socket."""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
from enum import StrEnum

import psutil
from pydantic import BaseModel

GATE_COMMAND = (sys.executable, "-u", __file__)


class GateRole(StrEnum):
    PARENT = "gated-parent"
    CHILD = "gated-child"


class ProcessReceipt(BaseModel):
    pid: int
    parent_pid: int
    created: float


def start_descendant(
    role: GateRole,
    port: int,
    stdio: str,
    *,
    detached: bool = False,
    spawn_on_eof: bool = False,
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        (
            *GATE_COMMAND,
            "--scenario",
            role,
            "--gate-port",
            str(port),
            *(("--detached-children",) if detached else ()),
            *(("--spawn-on-eof",) if spawn_on_eof else ()),
        ),
        # Windows stdin initialization must not share the root's pending pipe read.
        stdin=subprocess.PIPE if spawn_on_eof else subprocess.DEVNULL,
        stdout=None if stdio == "inherit" else subprocess.DEVNULL,
        stderr=None if stdio == "inherit" else subprocess.DEVNULL,
        start_new_session=detached and os.name != "nt",
    )


def hold_process_gate(
    port: int,
    *,
    spawn_child: bool,
    detached: bool = False,
    spawn_on_eof: bool = False,
) -> None:
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    def launch_child() -> subprocess.Popen[bytes] | None:
        return (
            start_descendant(GateRole.CHILD, port, "inherit", detached=detached)
            if spawn_child
            else None
        )

    child = None if spawn_on_eof else launch_child()
    with socket.create_connection(("127.0.0.1", port), timeout=5) as gate:
        gate.settimeout(None)
        receipt = ProcessReceipt(
            pid=os.getpid(),
            parent_pid=os.getppid(),
            created=psutil.Process().create_time(),
        )
        gate.sendall((receipt.model_dump_json() + "\n").encode())
        if spawn_on_eof:
            sys.stdin.buffer.read()
            child = launch_child()
        gate.recv(1)
    if child is not None:
        child.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", type=GateRole, required=True)
    parser.add_argument("--gate-port", type=int, required=True)
    parser.add_argument("--detached-children", action="store_true")
    parser.add_argument("--spawn-on-eof", action="store_true")
    args = parser.parse_args()
    hold_process_gate(
        args.gate_port,
        spawn_child=args.scenario == GateRole.PARENT,
        detached=args.detached_children,
        spawn_on_eof=args.spawn_on_eof,
    )


if __name__ == "__main__":
    main()
