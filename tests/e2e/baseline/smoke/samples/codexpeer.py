"""A local Responses peer that asks the real Codex CLI for one shell write."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from band.adapters.codex import CodexAdapterConfig
from band.integrations.codex.rpc_base import RpcEvent
from band.integrations.codex.stdio_client import CodexStdioClient
from band.integrations.codex.types import (
    CodexApprovalMethod,
    CodexItemType,
    CodexRequestMethod,
)
from band.integrations.uvicorn_server import ManagedUvicornServer

NATIVE_DEADLINE_S = 60
# The local peer implements this model's flat shell-tool wire format.
SHELL_MODEL = "gpt-5.3-codex"


class ShellPeer:
    """Emit one non-escalated command, then close after its tool result."""

    def __init__(self, command: str, workdir: Path, closing: str) -> None:
        self.command = command
        self.workdir = workdir
        self.closing = closing
        self.calls = 0

    def _shell_call(self, tools: list[dict[str, Any]]) -> dict[str, Any]:
        names = {tool.get("name") for tool in tools}
        if "exec_command" in names:
            name = "exec_command"
        elif "shell_command" in names:
            name = "shell_command"
        else:
            raise AssertionError(f"Codex advertised no supported shell tool: {names}")
        match name:
            case "exec_command":
                arguments = {"cmd": self.command}
            case "shell_command":
                arguments = {"command": self.command, "workdir": str(self.workdir)}
        self.calls += 1
        return {
            "type": "function_call",
            "call_id": "shell-write",
            "name": name,
            "arguments": json.dumps(arguments),
        }

    async def respond(self, request: Request) -> Response:
        body = await request.json()
        if self.calls == 0:
            item = self._shell_call(body["tools"])
        else:
            assert any(
                part.get("type") == "function_call_output" for part in body["input"]
            ), "Codex did not return the shell result"
            item = {
                "type": "message",
                "id": "closing",
                "role": "assistant",
                "content": [{"type": "output_text", "text": self.closing}],
            }
        events = [
            {"type": "response.created", "response": {"id": f"response-{self.calls}"}},
            {"type": "response.output_item.done", "item": item},
            {
                "type": "response.completed",
                "response": {
                    "id": f"response-{self.calls}",
                    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                },
            },
        ]
        content = "".join(
            f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
        )
        return Response(content, media_type="text/event-stream")


@asynccontextmanager
async def shell_peer(peer: ShellPeer) -> AsyncIterator[str]:
    server = ManagedUvicornServer(
        Starlette(routes=[Route("/responses", peer.respond, methods=["POST"])]),
        host="127.0.0.1",
        port=0,
    )
    try:
        await server.start()
        yield f"http://127.0.0.1:{server.bound_port}"
    finally:
        await server.stop()


@asynccontextmanager
async def native_client(
    config: CodexAdapterConfig, home: Path, workdir: Path, url: str
) -> AsyncIterator[CodexStdioClient]:
    home.mkdir()
    (home / "config.toml").write_text(
        'model_provider = "shellpeer"\n'
        "[model_providers.shellpeer]\n"
        'name = "Local shell peer"\n'
        f"base_url = {json.dumps(url)}\n"
        'wire_api = "responses"\n'
        "requires_openai_auth = false\n"
        "supports_websockets = false\n",
        encoding="utf-8",
    )
    client = CodexStdioClient(
        command=config.codex_command, cwd=str(workdir), env={"CODEX_HOME": str(home)}
    )
    try:
        async with asyncio.timeout(NATIVE_DEADLINE_S):
            await client.connect()
            await client.initialize(
                client_name="approval-smoke",
                client_title="Approval smoke",
                client_version="1",
                experimental_api=config.experimental_api,
            )
        yield client
    finally:
        await client.close()


async def start_shell_turn(
    client: CodexStdioClient, config: CodexAdapterConfig, workdir: Path
) -> None:
    thread = await client.request(
        CodexRequestMethod.THREAD_START,
        {
            "model": SHELL_MODEL,
            "cwd": str(workdir),
            "approvalPolicy": config.approval_policy,
            "sandbox": config.sandbox,
        },
    )
    await client.request(
        CodexRequestMethod.TURN_START,
        {
            "threadId": thread["thread"]["id"],
            "input": [{"type": "text", "text": "Execute the requested shell tool."}],
        },
    )


async def required_approval(client: CodexStdioClient) -> RpcEvent:
    while True:
        event = await client.recv_event()
        match event.method:
            case CodexApprovalMethod.COMMAND_EXECUTION:
                return event
            case "turn/completed":
                raise AssertionError(
                    "Codex completed without requesting shell approval"
                )
            case "transport/closed" | "error":
                raise AssertionError(f"Codex failed before approval: {event.params}")


async def closing_reply(client: CodexStdioClient) -> tuple[str, int]:
    replies: list[str] = []
    extra_approvals = 0
    while True:
        event = await client.recv_event()
        match event.method:
            case CodexApprovalMethod.COMMAND_EXECUTION:
                extra_approvals += 1
                assert event.id is not None
                await client.respond(event.id, {"decision": "decline"})
            case "item/completed":
                assert isinstance(event.params, dict)
                item = event.params["item"]
                if item.get("type") == CodexItemType.AGENT_MESSAGE:
                    replies.append(item["text"])
            case "turn/completed":
                assert isinstance(event.params, dict)
                assert event.params["turn"]["status"] == "completed", event.params
                return "".join(replies), extra_approvals
            case "transport/closed" | "error":
                raise AssertionError(f"Codex failed after approval: {event.params}")
