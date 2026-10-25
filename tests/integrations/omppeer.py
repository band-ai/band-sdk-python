"""Launch one focused OMP protocol peer for subprocess boundary tests."""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import threading

from pydantic import JsonValue

from band.integrations.ompwire import (
    OMP_FRAME_BYTES,
    OMP_NATIVE_PROTOCOL_VERSION,
    OMP_REASSEMBLED_BYTES,
    OmpFrameType,
)
from tests.integrations.ompframes import (
    Frame,
    encode_frame,
    send,
)
from tests.integrations.ompgate import GateRole, start_descendant
from tests.integrations.omppeers.base import RpcPeer
from tests.integrations.omppeers.callbacks import CallbackPeer
from tests.integrations.omppeers.completion import CompletionPeer
from tests.integrations.omppeers.process import ProcessLifecyclePeer
from tests.integrations.omppeers.transport import TransportPeer
from tests.integrations.ompscenarios import PeerScenario

READY_LIMIT_BYTES = 1024

PEER_TYPES = {
    scenario: peer_type
    for peer_type in (CallbackPeer, CompletionPeer, TransportPeer, ProcessLifecyclePeer)
    for scenario in peer_type.SCENARIOS
}


def create_peer(scenario: PeerScenario) -> RpcPeer:
    return PEER_TYPES[scenario](scenario)


def announce_ready(scenario: PeerScenario) -> None:
    ready: dict[str, JsonValue] = {
        "type": OmpFrameType.READY,
        "protocolVersion": 1,
        "supportedProtocolVersions": [1]
        if scenario == PeerScenario.NO_V2
        else [1, OMP_NATIVE_PROTOCOL_VERSION],
        "maxFrameBytes": OMP_FRAME_BYTES,
        "maxReassembledFrameBytes": OMP_REASSEMBLED_BYTES,
    }
    if scenario == PeerScenario.OVERSIZE_AFTER_READY:
        send_ready_with_oversize_frame(ready)
        return
    send(ready)


def send_ready_with_oversize_frame(ready: dict[str, JsonValue]) -> None:
    ready["maxFrameBytes"] = READY_LIMIT_BYTES
    frames = (
        ready,
        {"type": OmpFrameType.SESSION_SETTLED, "padding": "x" * READY_LIMIT_BYTES},
    )
    # One write keeps the oversized frame buffered behind its ready limits.
    sys.stdout.write(
        "\n".join(encode_frame(frame).decode("utf-8") for frame in frames) + "\n"
    )
    sys.stdout.flush()


def parse_peer_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario", type=PeerScenario, choices=tuple(PeerScenario), required=True
    )
    parser.add_argument("--gate-port", type=int)
    parser.add_argument("--detached-children", action="store_true")
    parser.add_argument("--spawn-on-eof", action="store_true")
    parser.add_argument(
        "--child-stdio", choices=("inherit", "discard"), default="inherit"
    )
    # Adapter launches also append native CLI flags this peer does not consume.
    args, _unknown = parser.parse_known_args()
    return args


def spawn_gated_descendant(args: argparse.Namespace) -> subprocess.Popen[bytes] | None:
    if args.gate_port is not None and args.scenario in {
        PeerScenario.DESCENDANTS,
        PeerScenario.NO_READY_DESCENDANTS,
        PeerScenario.LIFECYCLE,
        PeerScenario.EOF_DESCENDANTS,
    }:
        # The gate, not the peer's stdio EOF, owns these children's lifetime.
        return start_descendant(
            GateRole.PARENT,
            args.gate_port,
            args.child_stdio,
            detached=args.detached_children,
            spawn_on_eof=args.scenario == PeerScenario.EOF_DESCENDANTS,
        )
    return None


def main() -> None:
    args = parse_peer_arguments()
    managed_child = spawn_gated_descendant(args)
    if args.scenario == PeerScenario.STUBBORN and hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    peer = create_peer(args.scenario)
    if args.scenario not in {PeerScenario.NO_READY, PeerScenario.NO_READY_DESCENDANTS}:
        announce_ready(args.scenario)
    for line in sys.stdin:
        peer.handle(Frame.model_validate_json(line, strict=True))
    if managed_child is not None and managed_child.stdin is not None:
        managed_child.stdin.close()
    if args.scenario == PeerScenario.STUBBORN:
        threading.Event().wait()


if __name__ == "__main__":
    main()
