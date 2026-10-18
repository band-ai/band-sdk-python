"""A stdio peer that stays alive after shutdown until its owner reaps it."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path


def serve(
    trace_path: Path, *, reject_connect: bool = False, linger_after_eof: bool = True
) -> None:
    """Answer only handshake, hosted-model listing and runtime shutdown."""
    incoming = sys.stdin.buffer
    outgoing = sys.stdout.buffer
    with trace_path.open("a", encoding="utf-8") as trace:
        while header := incoming.readline():
            length = int(header.decode().split(":", 1)[1].strip())
            while incoming.readline().strip():
                pass
            request = json.loads(incoming.read(length))
            method = request["method"]
            trace.write(f"{method}\n")
            trace.flush()
            if "id" not in request:
                continue
            response: dict[str, object] = {
                "jsonrpc": "2.0",
                "id": request["id"],
            }
            match method:
                case "connect":
                    if reject_connect:
                        response["error"] = {
                            "code": -32000,
                            "message": "Connection rejected by test peer",
                        }
                    else:
                        response["result"] = {
                            "ok": True,
                            "protocolVersion": 3,
                            "version": "1.0.0",
                        }
                case "models.list":
                    response["result"] = {"models": []}
                case "runtime.shutdown":
                    response["result"] = {}
                case _:
                    response["error"] = {
                        "code": -32601,
                        "message": "Unsupported test peer method",
                    }
            payload = json.dumps(response).encode()
            outgoing.write(f"Content-Length: {len(payload)}\r\n\r\n".encode())
            outgoing.write(payload)
            outgoing.flush()

    # Custody tests distinguish transport EOF from physical process exit.
    sys.stdout.close()
    sys.stderr.close()
    if linger_after_eof:
        threading.Event().wait()


if __name__ == "__main__":
    serve(Path(sys.argv[1]), reject_connect="--reject-connect" in sys.argv[2:])
