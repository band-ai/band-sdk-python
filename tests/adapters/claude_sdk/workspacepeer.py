"""A minimal subprocess peer for SDK permission and workspace integration tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def emit(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def perform(tool_name: str, arguments: dict[str, str]) -> str:
    path = Path(arguments["file_path"])
    if tool_name == "Write":
        path.write_text(arguments["content"])
    return path.read_text()


def main() -> None:
    tool_name = ""
    for line in sys.stdin:
        message = json.loads(line)
        match message["type"]:
            case "control_request":
                emit(
                    {
                        "type": "control_response",
                        "response": {
                            "subtype": "success",
                            "request_id": message["request_id"],
                            "response": {},
                        },
                    }
                )
            case "user":
                request = json.loads(message["message"]["content"])
                tool_name = request["tool_name"]
                emit(
                    {
                        "type": "control_request",
                        "request_id": "permission",
                        "request": {
                            "subtype": "can_use_tool",
                            "tool_name": tool_name,
                            "input": request["input"],
                            "tool_use_id": "file-operation",
                        },
                    }
                )
            case "control_response":
                response = message["response"]
                denied = (
                    response["subtype"] == "error"
                    or response["response"]["behavior"] == "deny"
                )
                result = (
                    "denied"
                    if denied
                    else perform(tool_name, response["response"]["updatedInput"])
                )
                emit(
                    {
                        "type": "result",
                        "subtype": "success",
                        "duration_ms": 1,
                        "duration_api_ms": 1,
                        "is_error": denied,
                        "num_turns": 1,
                        "session_id": "workspace-peer",
                        "result": result,
                    }
                )


if __name__ == "__main__":
    main()
