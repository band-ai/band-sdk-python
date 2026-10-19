"""A real stdio app-server boundary for host probes without provider turns."""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any


def emit(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main() -> None:
    record = Path(sys.argv[1])
    settings = json.loads(sys.argv[2])
    if diagnostic := settings.get("diagnostic"):
        sys.stdout.write(
            diagnostic
            + "\n"
            + json.dumps([diagnostic])
            + "\n"
            + json.dumps({"diagnostic": diagnostic})
            + "\n"
        )
        sys.stdout.flush()
        sys.stderr.write(diagnostic + "\n")
        sys.stderr.flush()
    if settings.get("startup_hang"):
        threading.Event().wait()
    for line in sys.stdin:
        message = json.loads(line)
        with record.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        **message,
                        "pid": os.getpid(),
                        "marker": os.environ["CODEX_PROBE_MARKER"],
                    }
                )
                + "\n"
            )
        if "id" not in message:
            continue
        method = message["method"]
        if method == "model/list":
            cursor = message["params"].get("cursor")
            pages = settings.get("pages", {"": {"data": [], "nextCursor": None}})
            reply = pages.get(cursor or "", {})
            if settings.get("endless_pages"):
                page = int(cursor or "0")
                reply = {"data": [], "nextCursor": str(page + 1)}
        else:
            reply = settings.get("replies", {}).get(method, {})
        if reply.get("hang"):
            threading.Event().wait()
        elif "error" in reply:
            emit({"id": message["id"], "error": reply["error"]})
        else:
            emit({"id": message["id"], "result": reply})


if __name__ == "__main__":
    main()
