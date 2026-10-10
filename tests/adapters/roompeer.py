"""A real subprocess peer for Codex and Claude workspace lifecycle tests."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import threading
import time
from enum import StrEnum
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from band.runtime.tools import BandTool

WRITE_PREFIX = "WRITE="


class PeerCommand(StrEnum):
    READ = "READ"
    LOSE_TRANSPORT = "LOSE_TRANSPORT"
    EXIT_PROCESS = "EXIT_PROCESS"


def write_instruction(marker: str) -> str:
    return f"{WRITE_PREFIX}{marker}"


def emit(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def perform(tool_name: str, arguments: dict[str, str]) -> str:
    path = Path(arguments["file_path"])
    if tool_name == "Write":
        path.write_text(arguments["content"])
    return path.read_text()


def reply_text(tool_name: str, value: str) -> str:
    return f"Read: {value}" if tool_name == "Read" else value


async def reply(url: str, content: str) -> None:
    async with (
        streamable_http_client(url) as (read, write, *_),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            BandTool.SEND_MESSAGE, {"content": content, "mentions": ["@alice"]}
        )
        if result.isError:
            raise RuntimeError(f"The Band reply tool failed: {result.content}")


def file_request(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        marker = re.search(rf"{re.escape(WRITE_PREFIX)}([a-zA-Z0-9-]+)", content)
        arguments = {"file_path": "notes.txt"}
        if marker is not None:
            arguments["content"] = marker.group(1)
        return {
            "tool_name": "Write" if marker is not None else "Read",
            "input": arguments,
        }


def lose_transport() -> None:
    # An invalid response id faults the real reader while this child stays alive.
    emit({"id": []})
    threading.Event().wait()


def initialize(failure: Path | None, hold: Path | None) -> bool:
    Path("starting.txt").touch()
    while hold is not None and hold.exists():
        time.sleep(0.01)
    return failure is not None and failure.exists()


def codex(
    message: dict[str, Any], failure: Path | None, hold: Path | None, threads: set[str]
) -> None:
    match message["method"]:
        case "initialize":
            failed = initialize(failure, hold)
            answer = (
                {"error": {"code": -32000, "message": "startup failed"}}
                if failed
                else {"result": {}}
            )
            emit({"id": message["id"], **answer})
        case "thread/start" | "thread/resume":
            thread_id = f"peer-{os.getpid()}-{len(threads)}"
            threads.add(thread_id)
            emit({"id": message["id"], "result": {"thread": {"id": thread_id}}})
        case "turn/start":
            if message["params"]["threadId"] not in threads:
                emit(
                    {
                        "id": message["id"],
                        "error": {"code": -32000, "message": "unknown thread"},
                    }
                )
                return
            emit({"id": message["id"], "result": {"turn": {"id": "peer-turn"}}})
            content = "\n".join(
                item["text"]
                for item in message["params"]["input"]
                if item["type"] == "text"
            )
            if PeerCommand.EXIT_PROCESS in content:
                os._exit(0)
            if PeerCommand.LOSE_TRANSPORT in content:
                lose_transport()
            request = file_request(content)
            result = perform(request["tool_name"], request["input"])
            emit(
                {
                    "id": "peer-reply",
                    "method": "item/tool/call",
                    "params": {
                        "tool": BandTool.SEND_MESSAGE,
                        "arguments": {
                            "content": reply_text(request["tool_name"], result),
                            "mentions": ["@alice"],
                        },
                    },
                }
            )
            response = json.loads(sys.stdin.readline())
            if not response.get("result", {}).get("success"):
                raise RuntimeError("The Band reply tool failed")
            emit(
                {
                    "method": "turn/completed",
                    "params": {
                        "turn": {
                            "id": "peer-turn",
                            "status": "completed",
                            "items": [],
                            "error": None,
                        }
                    },
                }
            )
        case _:
            if "id" in message:
                emit({"id": message["id"], "result": {}})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fail-startup", type=Path)
    parser.add_argument("--hold-startup", type=Path)
    parser.add_argument("--stay", action="store_true")
    parser.add_argument("--band-url")
    options = parser.parse_args()
    tool_name = ""
    threads: set[str] = set()
    for line in sys.stdin:
        message = json.loads(line)
        if "method" in message:
            codex(message, options.fail_startup, options.hold_startup, threads)
            continue
        match message["type"]:
            case "control_request":
                failed = message["request"]["subtype"] == "initialize" and initialize(
                    options.fail_startup, options.hold_startup
                )
                answer = (
                    {"subtype": "error", "error": "startup failed"}
                    if failed
                    else {"subtype": "success", "response": {}}
                )
                emit(
                    {
                        "type": "control_response",
                        "response": {"request_id": message["request_id"], **answer},
                    }
                )
            case "user":
                content = message["message"]["content"]
                if any(
                    command in content
                    for command in (
                        PeerCommand.LOSE_TRANSPORT,
                        PeerCommand.EXIT_PROCESS,
                    )
                ):
                    # The SDK transport delivers EOF only after the child exits.
                    return
                request = file_request(content)
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
                if options.band_url is not None and not denied:
                    asyncio.run(reply(options.band_url, reply_text(tool_name, result)))
                emit(
                    {
                        "type": "assistant",
                        "message": {
                            "model": "peer",
                            "content": [{"type": "text", "text": result}],
                        },
                        "session_id": "workspace-peer",
                    }
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
    if options.stay:
        threading.Event().wait()


if __name__ == "__main__":
    main()
