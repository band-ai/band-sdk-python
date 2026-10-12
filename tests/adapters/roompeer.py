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
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from band.runtime.tools import BandTool

WRITE_PREFIX = "WRITE="
MEMO_PREFIX = "MEMO="
PEER_PROVIDER = "peer"


class PeerCommand(StrEnum):
    READ = "READ"
    LOSE_TRANSPORT = "LOSE_TRANSPORT"
    EXIT_PROCESS = "EXIT_PROCESS"
    RECALL = "RECALL"
    TOOLS = "TOOLS"


def write_instruction(marker: str) -> str:
    return f"{WRITE_PREFIX}{marker}"


def memo_instruction(marker: str) -> str:
    """Keep ``marker`` only in the thread's private context."""
    return f"{MEMO_PREFIX}{marker}"


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


class AppServer:
    """A Codex app-server whose threads outlive the process, like rollouts.

    A thread is saved under ``home`` from its first turn on: its tools and a
    private memo that no Band message ever carries. Resuming restores both
    and ignores tools sent with the request, as Codex does; resuming a
    thread that was never saved fails with Codex's own refusal.
    """

    def __init__(self, home: Path, failure: Path | None, hold: Path | None) -> None:
        self.home = home
        self.failure = failure
        self.hold = hold
        self.loaded: dict[str, dict[str, Any]] = {}
        self.turns = 0

    def saved(self, thread_id: str) -> Path:
        return self.home / f"{thread_id}.json"

    def save(self, thread_id: str) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.saved(thread_id).write_text(json.dumps(self.loaded[thread_id]))

    def answer(self, message: dict[str, Any], thread_id: str) -> None:
        thread = self.loaded[thread_id]
        emit(
            {
                "id": message["id"],
                "result": {
                    "thread": {"id": thread_id},
                    "modelProvider": PEER_PROVIDER,
                    "cwd": message["params"].get("cwd") or os.getcwd(),
                    "tools": thread["tools"],
                },
            }
        )

    def handle(self, message: dict[str, Any]) -> None:
        match message["method"]:
            case "initialize":
                failed = initialize(self.failure, self.hold)
                answer = (
                    {"error": {"code": -32000, "message": "startup failed"}}
                    if failed
                    else {"result": {}}
                )
                emit({"id": message["id"], **answer})
            case "thread/start":
                thread_id = f"peer-thread-{uuid.uuid4().hex[:12]}"
                tools = [tool["name"] for tool in message["params"]["dynamicTools"]]
                self.loaded[thread_id] = {"tools": tools, "memo": None}
                self.answer(message, thread_id)
            case "thread/resume":
                thread_id = message["params"]["threadId"]
                if not self.saved(thread_id).exists():
                    emit(
                        {
                            "id": message["id"],
                            "error": {
                                "code": -32600,
                                "message": f"no rollout found for thread id {thread_id}",
                            },
                        }
                    )
                    return
                self.loaded[thread_id] = json.loads(self.saved(thread_id).read_text())
                self.answer(message, thread_id)
            case "turn/start":
                self.turn(message)
            case _:
                if "id" in message:
                    emit({"id": message["id"], "result": {}})

    def turn(self, message: dict[str, Any]) -> None:
        thread_id = message["params"]["threadId"]
        if thread_id not in self.loaded:
            emit(
                {
                    "id": message["id"],
                    "error": {"code": -32000, "message": "unknown thread"},
                }
            )
            return
        self.turns += 1
        turn_id = f"{thread_id}-turn-{os.getpid()}-{self.turns}"
        emit({"id": message["id"], "result": {"turn": {"id": turn_id}}})
        self.save(thread_id)
        content = "\n".join(
            item["text"]
            for item in message["params"]["input"]
            if item["type"] == "text"
        )
        if PeerCommand.EXIT_PROCESS in content:
            os._exit(0)
        if PeerCommand.LOSE_TRANSPORT in content:
            lose_transport()
        reply = self.reply_to(content, thread_id)
        self.call_tool(
            thread_id,
            turn_id,
            BandTool.SEND_MESSAGE,
            {"content": reply, "mentions": ["@alice"]},
        )
        emit(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": thread_id,
                    "turn": {
                        "id": turn_id,
                        "status": "completed",
                        "items": [],
                        "error": None,
                    },
                },
            }
        )

    def reply_to(self, content: str, thread_id: str) -> str:
        thread = self.loaded[thread_id]
        if (memo := re.search(rf"{MEMO_PREFIX}([a-zA-Z0-9-]+)", content)) is not None:
            thread["memo"] = memo.group(1)
            self.save(thread_id)
            return "Noted."
        if PeerCommand.RECALL in content:
            return f"Recalled: {thread['memo']}"
        if PeerCommand.TOOLS in content:
            return f"Tools: {sorted(thread['tools'])}"
        request = file_request(content)
        return reply_text(
            request["tool_name"], perform(request["tool_name"], request["input"])
        )

    def call_tool(
        self, thread_id: str, turn_id: str, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        emit(
            {
                "id": f"call-{turn_id}",
                "method": "item/tool/call",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "callId": f"call-{turn_id}",
                    "tool": tool,
                    "arguments": arguments,
                },
            }
        )
        response = json.loads(sys.stdin.readline())
        if not response.get("result", {}).get("success"):
            raise RuntimeError(f"The Band tool {tool} failed: {response}")
        return response


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fail-startup", type=Path)
    parser.add_argument("--hold-startup", type=Path)
    parser.add_argument("--stay", action="store_true")
    parser.add_argument("--band-url")
    parser.add_argument("--home", type=Path, default=Path("codex-home"))
    options = parser.parse_args()
    tool_name = ""
    server = AppServer(options.home, options.fail_startup, options.hold_startup)
    for line in sys.stdin:
        message = json.loads(line)
        if "method" in message:
            server.handle(message)
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
