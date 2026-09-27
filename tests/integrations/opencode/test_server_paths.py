"""Characterize OpenCode path approvals against an installed serve process."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Any

import httpx
import pytest

from band.integrations.opencode.client import HttpOpencodeClient

_PROVIDER_ID = "path-probe"
_MODEL_ID = "model"
_MODEL_NAME = f"{_PROVIDER_ID}/{_MODEL_ID}"
_READ_TOOL = "read"
_EXTERNAL_PERMISSION = "external_directory"


class LocalModelServer(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # HTTPServer's reverse DNS lookup can stall on CI runners.
        TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]


def _directory_alias(alias: Path, target: Path) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(alias), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
        return
    alias.symlink_to(target, target_is_directory=True)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _model_server(target: Path) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            size = int(self.headers["Content-Length"])
            request = json.loads(self.rfile.read(size))
            tools = {tool["function"]["name"] for tool in request.get("tools", [])}
            used_tool = any(
                message.get("role") == "tool" for message in request["messages"]
            )
            if _READ_TOOL in tools and not used_tool:
                delta: dict[str, Any] = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_read",
                            "type": "function",
                            "function": {
                                "name": _READ_TOOL,
                                "arguments": json.dumps({"filePath": str(target)}),
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            else:
                delta = {"role": "assistant", "content": "done"}
                finish = "stop"

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for content, reason in ((delta, None), ({}, finish)):
                frame = {
                    "id": "chatcmpl-path-probe",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": _MODEL_ID,
                    "choices": [
                        {"index": 0, "delta": content, "finish_reason": reason}
                    ],
                }
                self.wfile.write(f"data: {json.dumps(frame)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = LocalModelServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@contextmanager
def _opencode_server(workdir: Path, model_url: str) -> Iterator[str]:
    executable = shutil.which("opencode")
    if executable is None:
        pytest.skip("opencode serve is not installed")

    port = _free_port()
    config = {
        "model": _MODEL_NAME,
        "small_model": _MODEL_NAME,
        "provider": {
            _PROVIDER_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Path probe",
                "options": {"baseURL": model_url, "apiKey": "local-probe"},
                "models": {
                    _MODEL_ID: {
                        "name": "Path probe",
                        "limit": {"context": 128000, "output": 4096},
                    }
                },
            }
        },
        "permission": {_EXTERNAL_PERMISSION: "ask", _READ_TOOL: "allow"},
    }
    env = {**os.environ, "OPENCODE_CONFIG_CONTENT": json.dumps(config)}
    process = subprocess.Popen(
        [executable, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
        cwd=workdir,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=base_url, timeout=2) as client:
            for _ in range(50):
                if process.poll() is not None:
                    pytest.fail("opencode serve exited before becoming healthy")
                try:
                    client.get("/global/health").raise_for_status()
                    break
                except httpx.HTTPError:
                    time.sleep(0.2)
            else:
                pytest.fail("opencode serve did not become healthy")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


async def _permission_for_read(
    base_url: str, directory: Path, target: Path
) -> str | None:
    client = HttpOpencodeClient(base_url=base_url, directory=str(directory))
    headers = {"x-opencode-directory": str(directory)}
    params = {"directory": str(directory)}
    session_id: str | None = None
    try:
        session_id = (await client.create_session(title="Path probe"))["id"]
        await client.prompt_async(
            session_id,
            parts=[{"type": "text", "text": "Read the requested file."}],
            model={"providerID": _PROVIDER_ID, "modelID": _MODEL_ID},
        )
        async with httpx.AsyncClient(base_url=base_url, timeout=5) as monitor:
            for _ in range(100):
                response = await monitor.get(
                    "/permission", headers=headers, params=params
                )
                response.raise_for_status()
                for request in response.json():
                    if request.get("sessionID") == session_id:
                        return str(request["permission"])
                messages = await monitor.get(
                    f"/session/{session_id}/message", headers=headers, params=params
                )
                messages.raise_for_status()
                for message in messages.json():
                    for part in message.get("parts", []):
                        if (
                            part.get("type") == "tool"
                            and part.get("tool") == _READ_TOOL
                        ):
                            state = part.get("state", {})
                            if state.get("status") == "completed":
                                return None
                            if state.get("status") == "error":
                                pytest.fail(
                                    f"OpenCode read failed: {state.get('error')}"
                                )
                await asyncio.sleep(0.1)
        pytest.fail(f"OpenCode did not read or ask about {target}")
    finally:
        if session_id is not None:
            await client.abort_session(session_id)
        await client.close()


@pytest.mark.asyncio
async def test_server_distinguishes_link_spelling_from_physical_and_external_paths(
    tmp_path: Path,
) -> None:
    physical = tmp_path / "physical"
    physical.mkdir()
    alias = tmp_path / "alias"
    _directory_alias(alias, physical)
    inside = physical / "inside.txt"
    inside.write_text("inside")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    external_directory = tmp_path / "external"
    external_directory.mkdir()
    (external_directory / "outside.txt").write_text("outside through link")
    external_alias = physical / "external-link"
    _directory_alias(external_alias, external_directory)
    alias_permission = None if sys.platform == "win32" else _EXTERNAL_PERMISSION

    with (
        _model_server(inside) as model_url,
        _opencode_server(physical, model_url) as base_url,
    ):
        client = HttpOpencodeClient(base_url=base_url, directory=str(alias))
        try:
            assert Path(await client.get_server_directory()) == physical.resolve()
        finally:
            await client.close()
        assert await _permission_for_read(base_url, alias, inside) is None

    with (
        _model_server(alias / "inside.txt") as model_url,
        _opencode_server(physical, model_url) as base_url,
    ):
        assert (
            await _permission_for_read(base_url, alias, alias / "inside.txt")
            == alias_permission
        )
        assert (
            await _permission_for_read(base_url, physical, alias / "inside.txt")
            == alias_permission
        )

    with (
        _model_server(outside) as model_url,
        _opencode_server(physical, model_url) as base_url,
    ):
        assert await _permission_for_read(base_url, alias, outside) == (
            _EXTERNAL_PERMISSION
        )

    with (
        _model_server(alias / "external-link" / "outside.txt") as model_url,
        _opencode_server(physical, model_url) as base_url,
    ):
        assert (
            await _permission_for_read(
                base_url, alias, alias / "external-link" / "outside.txt"
            )
            == _EXTERNAL_PERMISSION
        )
