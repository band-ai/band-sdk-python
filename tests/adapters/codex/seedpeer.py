"""A fault-injection app-server recording native prompts at the stdio boundary."""

from __future__ import annotations

import argparse
import json
import sys
from enum import StrEnum
from pathlib import Path
from typing import Any

from band.integrations.codex.types import (
    DYNAMIC_TOOL_CALL_METHOD,
    CodexItemType,
    CodexRequestMethod,
    CodexTurnStatus,
)
from band.runtime.tools import BandTool


class Step(StrEnum):
    UPDATE = "update"
    COMPLETE = CodexTurnStatus.COMPLETED
    FAIL = CodexTurnStatus.FAILED
    INTERRUPT = CodexTurnStatus.INTERRUPTED
    CANCEL = "cancel"
    POST_FAILS = "record post fails"
    POST_RECOVERS = "record post recovers"


def emit(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def native_turn_id(number: int) -> str:
    """Identify a peer turn consistently in its response and stop assertion."""
    return f"turn-{number}"


def native_thread_id(number: int) -> str:
    """Identify an external native thread across peer process restarts."""
    return f"thread-{number}"


INITIALIZE_FAILURE = "injected initialization failure"


def completed(thread_id: str, turn_id: str, status: str) -> None:
    """End the named native turn after its outstanding work has settled."""
    emit(
        {
            "method": "turn/completed",
            "params": {
                "threadId": thread_id,
                "turn": {"id": turn_id, "status": status},
            },
        }
    )


def serve(
    trace: Path,
    action: Path,
    fail_initialize_once: Path | None = None,
    reply_text: str | None = None,
) -> None:
    """Answer supported requests and inject explicitly selected external faults."""
    threads = (
        sum(
            json.loads(line).get("method") == CodexRequestMethod.THREAD_START
            for line in trace.read_text().splitlines()
        )
        if trace.exists()
        else 0
    )
    known_threads = {native_thread_id(number) for number in range(1, threads + 1)}
    turns = 0
    active: tuple[str, str] | None = None
    pending_call: str | None = None
    for line in sys.stdin:
        request = json.loads(line)
        with trace.open("a") as recording:
            recording.write(json.dumps(request) + "\n")
        if "method" not in request:
            if pending_call is None or request.get("id") != pending_call:
                raise ValueError("Unexpected Band tool response")
            if not request.get("result", {}).get("success"):
                raise ValueError("Band decline tool failed")
            assert active is not None
            completed(*active, CodexTurnStatus.COMPLETED)
            active, pending_call = None, None
            continue
        if "id" not in request:
            if request["method"] != "initialized":
                raise ValueError("Unsupported client notification")
            continue
        params = request.get("params", {})
        match request["method"]:
            case CodexRequestMethod.INITIALIZE:
                if (
                    fail_initialize_once is not None
                    and not fail_initialize_once.exists()
                ):
                    fail_initialize_once.touch()
                    emit(
                        {
                            "id": request["id"],
                            "error": {"code": -32603, "message": INITIALIZE_FAILURE},
                        }
                    )
                    continue
                result = {"userAgent": "seed-model-peer"}
            case CodexRequestMethod.THREAD_START:
                threads += 1
                thread_id = native_thread_id(threads)
                known_threads.add(thread_id)
                result = {"thread": {"id": thread_id}}
            case CodexRequestMethod.THREAD_RESUME:
                if params.get("threadId") not in known_threads:
                    emit(
                        {
                            "id": request["id"],
                            "error": {
                                "code": -32602,
                                "message": "unknown native thread",
                            },
                        }
                    )
                    continue
                result = {"thread": {"id": params["threadId"]}}
            case CodexRequestMethod.TURN_START:
                if active is not None:
                    raise ValueError("A turn is still active")
                turns += 1
                active = (params["threadId"], native_turn_id(turns))
                result = {"turn": {"id": active[1]}}
            case CodexRequestMethod.TURN_INTERRUPT:
                if active != (params.get("threadId"), params.get("turnId")):
                    raise ValueError("Interrupt names no active turn")
                emit({"id": request["id"], "result": {}})
                completed(*active, CodexTurnStatus.INTERRUPTED)
                active, pending_call = None, None
                continue
            case _:
                emit(
                    {
                        "id": request["id"],
                        "error": {"code": -32601, "message": "unsupported request"},
                    }
                )
                continue
        emit({"id": request["id"], "result": result})
        if request["method"] != CodexRequestMethod.TURN_START:
            continue
        outcome = Step(action.read_text())
        if outcome not in {Step.COMPLETE, Step.FAIL, Step.INTERRUPT, Step.CANCEL}:
            raise ValueError("Unsupported native turn outcome")
        assert active is not None
        thread_id, turn_id = active
        match outcome:
            case Step.CANCEL:
                continue
            case Step.COMPLETE:
                if reply_text is None:
                    pending_call = f"decline-{turns}"
                    emit(
                        {
                            "id": pending_call,
                            "method": DYNAMIC_TOOL_CALL_METHOD,
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "callId": pending_call,
                                "tool": BandTool.NO_REPLY,
                                "arguments": {},
                            },
                        }
                    )
                    continue
                emit(
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": thread_id,
                            "turnId": turn_id,
                            "item": {
                                "type": CodexItemType.AGENT_MESSAGE,
                                "id": f"reply-{turns}",
                                "text": reply_text,
                                "phase": "final_answer",
                            },
                        },
                    }
                )
        completed(thread_id, turn_id, outcome)
        active = None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    parser.add_argument("action", type=Path)
    parser.add_argument("--fail-initialize-once", type=Path)
    parser.add_argument("--reply-text")
    options = parser.parse_args()
    serve(
        options.trace, options.action, options.fail_initialize_once, options.reply_text
    )


if __name__ == "__main__":
    main()
