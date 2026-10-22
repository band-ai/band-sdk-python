from __future__ import annotations

from typing import Any

from remote_agent.band.arithmetic import write_runtime_configuration
from remote_agent.band.generic import immutable_file
from remote_agent.band.qualification import _immutable_bytes

CONFIGURATION = {
    "agent": {
        "provider": {"command": ["provider"]},
        "validator": {"command": ["validator"]},
    },
    "identity": "original",
}


def publish(kind: Any, path: Any, changed: bool = False) -> None:
    if kind == "source":
        immutable_file(path, "changed source" if changed else "qualified source")
    elif kind == "quality":
        _immutable_bytes(path, b"changed quality" if changed else b"qualified quality")
    else:
        write_runtime_configuration(
            path, dict(CONFIGURATION, identity="changed" if changed else "original")
        )
