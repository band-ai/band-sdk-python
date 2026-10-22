"""Explicit portable worker I/O and private-journal fixture; never executes a program."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from typing import Any

from contracts.spec.tools.paths import CONTRACTS_ROOT as ROOT
from remote_agent.storage import canonical, digest

program_hash = hashlib.sha256(b"# fixture artifact: not executable\n").hexdigest()
identifier = "turn:input-1:1"
artifact = {
    "path": "/fixture/artifacts/" + program_hash + ".py",
    "sha256": program_hash,
}
request = {
    "op": "handle",
    "artifact": {"path": "/program/program.py", "sha256": program_hash},
    "isolation": {
        "profile": "remote-agent/linux-pure-python/1",
        "memory_bytes": 268435456,
        "cpu_seconds": 1,
    },
    "event": {"id": "input-1", "type": "increment", "by": 1},
    "state": {"count": 0},
    "view": {"version": 0},
}
input = {
    "profile": "remote-agent/program-input/1",
    "id": identifier,
    "purpose": "turn",
    "program": artifact,
    "request": request,
    "timeout_ms": 1000,
    "max_output_bytes": 262144,
}
result = {
    "type": "handled",
    "state": {"count": 1},
    "message": None,
    "snapshot": {"count": 1},
}
execution = {
    "profile": "remote-agent/process-result/1",
    "stdout": base64.b64encode(canonical(result) + b"\n").decode(),
    "stderr": base64.b64encode(b"fixture diagnostic\x1f\n").decode(),
    "exit_code": 0,
    "failure": None,
    "complete": True,
    "elapsed_ms": 3,
}
fingerprint = digest(input)
output = {
    "profile": "remote-agent/program-output/1",
    "id": identifier,
    "input_hash": fingerprint,
    "execution": execution,
}
journal = []
for kind, data, status in [
    ("program_input", input, "started"),
    ("program_output", output, "completed"),
]:
    index: dict[str, Any] = {"fingerprint": fingerprint, "status": status}
    if status == "completed":
        index["execution"] = execution
    frame = {
        "sequence": len(journal) + 1,
        "kind": kind,
        "data": data,
        "updates": {"invocation:" + identifier: index},
        "previous": journal[-1]["hash"] if journal else "0" * 64,
    }
    frame["hash"] = digest(frame)
    journal.append(frame)
fixture = {
    "profile": "band-contract/invocation-fixture/1",
    "input": input,
    "output": output,
    "journal": journal,
    "recovered_result": result,
    "interrupted_result": "hold",
}
(ROOT / "spec/conformance/invocation-replay.json").write_text(
    json.dumps(fixture, indent=2, ensure_ascii=False) + "\n"
)
sys.stdout.write(
    "Wrote exact invocation input/output and two verified journal frames" + "\n"
)
