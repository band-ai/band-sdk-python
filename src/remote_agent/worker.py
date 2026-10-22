"""Callback inside Linux isolation; apply hard limits before evaluating source."""

from __future__ import annotations

import hashlib
import json
import sys
import types
from contextlib import redirect_stdout
from dataclasses import asdict
from pathlib import Path

from remote_agent.sandbox import restrict
from remote_agent.sdk import Delegate, Handled, Message, TurnContext
from remote_agent.storage import canonical
from remote_agent.types import JsonObject


def handled(result: Handled) -> JsonObject:
    if not isinstance(result.state, dict):
        raise ValueError("Program state must be a JSON object")
    if result.message is not None and (
        not isinstance(result.message, Message)
        or not isinstance(result.message.kind, str)
        or not result.message.kind
        or not isinstance(result.message.payload, dict)
    ):
        raise ValueError("Invalid program message")
    return {
        "type": "handled",
        "state": result.state,
        "message": asdict(result.message) if result.message is not None else None,
    }


def run(request: JsonObject) -> JsonObject:
    restrict(request.get("isolation"))
    artifact = request["artifact"]
    with Path(artifact["path"]).open("rb") as source:
        encoded = source.read(262_145)
    if (
        len(encoded) > 262_144
        or hashlib.sha256(encoded).hexdigest() != artifact["sha256"]
    ):
        raise ValueError("Program source hash mismatch")
    module = types.ModuleType("selected_program")
    exec(compile(encoded, artifact["path"], "exec"), module.__dict__)  # noqa: S102 -- The isolated callback worker evaluates hash-verified source.
    if request["op"] == "describe":
        schema, coverage = module.STATE_VERSION, module.COVERAGE
        if not isinstance(schema, str) or not schema or len(schema) > 100:
            raise ValueError("Invalid state schema version")
        if (
            not isinstance(coverage, dict)
            or set(coverage) != {"mode", "handles", "delegates"}
            or coverage["mode"] not in ("full", "partial", "llm_only")
            or any(
                not isinstance(coverage[key], list)
                or any(not isinstance(x, str) or not x for x in coverage[key])
                for key in ("handles", "delegates")
            )
            or coverage["mode"] != "full"
            and not coverage["delegates"]
        ):
            raise ValueError(
                "Declare full, partial, or llm_only coverage and delegation conditions"
            )
        state = module.initial(request["view"])
        if not isinstance(state, dict):
            raise ValueError("Initial state must be an object")
        return {"schema": schema, "coverage": coverage, "initial": state}
    if request["op"] != "handle":
        raise ValueError("Unknown callback")
    context = TurnContext(request["view"])
    result = module.handle(request["event"], request["state"], context)
    if isinstance(result, Delegate):
        if (
            not isinstance(result.reason, str)
            or not result.reason
            or len(result.reason) > 2000
            or result.scope not in ("turn", "ongoing")
            or type(result.repair) is not bool
        ):
            raise ValueError("Invalid delegation request")
        return dict(asdict(result), type="delegate")
    if isinstance(result, Handled):
        output = handled(result)
        if context.snapshot is not None and canonical(context.snapshot) != canonical(
            result.state
        ):
            raise ValueError("Staged snapshot differs from the completed state")
        return dict(output, snapshot=context.snapshot)
    raise ValueError("Expected Handled or Delegate")


def main() -> None:
    try:
        request = json.loads(sys.stdin.buffer.read(1_048_577))
        # The result occupies stdout. Print diagnostics use separately bounded stderr.
        # Low-level writes to either descriptor are still captured by the host.
        with redirect_stdout(sys.stderr):
            result = run(request)
        output = canonical(result)
    except Exception as error:  # noqa: BLE001 -- Arbitrary isolated callback exceptions become bounded worker results.
        output = canonical({"error": f"{type(error).__name__}: {error}"[:2000]})
    sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()
