"""Tests for codex stdio JSON-RPC client."""

from __future__ import annotations

import asyncio
import os
import sys
import textwrap
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from band.integrations.codex import (
    CodexJsonRpcError,
    CodexStdioClient,
    OverloadRetryPolicy,
)


@pytest.fixture
def fake_codex_server_script(tmp_path: Path) -> Path:
    """Write a fake stdio codex server used by client tests."""
    script = tmp_path / "fake_codex_server.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            import os
            import sys

            scenario = os.environ.get("FAKE_CODEX_SCENARIO", "basic")
            initialized = False
            sent_overload_once = False

            def send(payload):
                sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\\n")
                sys.stdout.flush()

            for raw in sys.stdin:
                raw = raw.strip()
                if not raw:
                    continue
                message = json.loads(raw)
                method = message.get("method")

                if method == "initialize":
                    send({"id": message["id"], "result": {"server": "fake_codex"}})
                    continue

                if method == "initialized":
                    initialized = True
                    continue

                if method == "thread/start":
                    if not initialized:
                        send({
                            "id": message["id"],
                            "error": {"code": -32002, "message": "Not initialized"},
                        })
                        continue

                    if scenario in {"retry_once", "always_overload"}:
                        if scenario == "always_overload" or not sent_overload_once:
                            sent_overload_once = True
                            send({
                                "id": message["id"],
                                "error": {
                                    "code": -32001,
                                    "message": "Server overloaded; retry later.",
                                },
                            })
                            continue

                    send({"id": message["id"], "result": {"thread": {"id": "thr_fake"}}})
                    send({"method": "thread/started", "params": {"thread": {"id": "thr_fake"}}})
                    continue

                if method == "turn/start":
                    send({
                        "id": message["id"],
                        "result": {
                            "turn": {
                                "id": "turn_fake",
                                "status": "inProgress",
                                "items": [],
                                "error": None,
                            }
                        },
                    })
                    send({
                        "method": "turn/started",
                        "params": {
                            "turn": {
                                "id": "turn_fake",
                                "status": "inProgress",
                                "items": [],
                                "error": None,
                            }
                        },
                    })

                    if scenario == "server_request":
                        send({
                            "id": "srv-tool-1",
                            "method": "item/tool/call",
                            "params": {
                                "threadId": "thr_fake",
                                "turnId": "turn_fake",
                                "callId": "call_1",
                                "tool": "echo",
                                "arguments": {"value": "ping"},
                            },
                        })
                        response_line = sys.stdin.readline()
                        if not response_line:
                            sys.exit(0)
                        response = json.loads(response_line)
                        result = response.get("result") or {}
                        status = "completed" if result.get("success") else "failed"
                        send({
                            "method": "item/completed",
                            "params": {
                                "item": {
                                    "type": "mcpToolCall",
                                    "id": "tool_1",
                                    "status": status,
                                }
                            },
                        })

                    send({
                        "method": "item/agentMessage/delta",
                        "params": {"itemId": "msg_1", "delta": "harness-ok"},
                    })
                    send({
                        "method": "item/completed",
                        "params": {
                            "item": {
                                "type": "agentMessage",
                                "id": "msg_1",
                                "text": "harness-ok",
                            }
                        },
                    })
                    send({
                        "method": "turn/completed",
                        "params": {
                            "turn": {
                                "id": "turn_fake",
                                "status": "completed",
                                "items": [],
                                "error": None,
                            }
                        },
                    })
                    continue

                if message.get("id") is not None and (
                    message.get("result") is not None or message.get("error") is not None
                ):
                    # Client response to server request.
                    continue

                if message.get("id") is not None:
                    send({
                        "id": message["id"],
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}",
                        },
                    })
            """
        ),
        encoding="utf-8",
    )
    return script


async def _build_client(
    fake_server_script: Path,
    *,
    scenario: str,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    retry_policy: OverloadRetryPolicy | None = None,
) -> CodexStdioClient:
    env = dict(os.environ)
    env["FAKE_CODEX_SCENARIO"] = scenario
    client = CodexStdioClient(
        command=[sys.executable, "-u", str(fake_server_script)],
        env=env,
        retry_policy=retry_policy,
        sleep=sleep,
    )
    await client.connect()
    await client.initialize(
        client_name="test_codex_client",
        client_title="Test Codex Client",
        client_version="0.1.0",
        experimental_api=True,
    )
    return client


def test_stdio_client_merges_custom_env_with_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BAND_PARENT_ENV", "parent-value")
    client = CodexStdioClient(
        command=["codex"],
        env={
            "BAND_CHILD_ONLY": "child-value",
            "BAND_PARENT_ENV": "overridden-value",
        },
    )

    assert client.env is not None
    assert client.env["BAND_CHILD_ONLY"] == "child-value"
    assert client.env["BAND_PARENT_ENV"] == "overridden-value"


@pytest.mark.asyncio
async def test_stdio_client_basic_lifecycle(fake_codex_server_script: Path) -> None:
    client = await _build_client(fake_codex_server_script, scenario="basic")
    try:
        thread_result = await client.request("thread/start", {"cwd": os.getcwd()})
        assert thread_result["thread"]["id"] == "thr_fake"

        turn_result = await client.request(
            "turn/start",
            {
                "threadId": "thr_fake",
                "input": [{"type": "text", "text": "ping"}],
            },
        )
        assert turn_result["turn"]["id"] == "turn_fake"

        methods: list[str] = []
        deltas = ""
        while True:
            event = await client.recv_event(timeout_s=1.0)
            methods.append(event.method)
            if event.method == "item/agentMessage/delta":
                params = event.params if isinstance(event.params, dict) else {}
                deltas += str(params.get("delta", ""))
            if event.method == "turn/completed":
                break

        assert "thread/started" in methods
        assert "turn/started" in methods
        assert deltas == "harness-ok"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stdio_client_routes_server_requests(
    fake_codex_server_script: Path,
) -> None:
    client = await _build_client(fake_codex_server_script, scenario="server_request")
    try:
        await client.request("thread/start", {"cwd": os.getcwd()})
        await client.request(
            "turn/start",
            {
                "threadId": "thr_fake",
                "input": [{"type": "text", "text": "use tool"}],
            },
        )

        saw_tool_request = False
        while True:
            event = await client.recv_event(timeout_s=1.0)
            if event.kind == "request":
                assert event.method == "item/tool/call"
                saw_tool_request = True
                await client.respond(
                    event.id,
                    {
                        "contentItems": [{"type": "inputText", "text": "pong"}],
                        "success": True,
                    },
                )
                continue
            if event.method == "turn/completed":
                break

        assert saw_tool_request is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stdio_client_retries_overload(fake_codex_server_script: Path) -> None:
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    retry_policy = OverloadRetryPolicy(
        max_attempts=3,
        base_delay_s=0.01,
        max_delay_s=0.05,
        jitter_s=0.0,
    )
    client = await _build_client(
        fake_codex_server_script,
        scenario="retry_once",
        sleep=fake_sleep,
        retry_policy=retry_policy,
    )
    try:
        thread_result = await client.request("thread/start", {"cwd": os.getcwd()})
        assert thread_result["thread"]["id"] == "thr_fake"
        assert delays == [0.01]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stdio_client_raises_after_retry_limit(
    fake_codex_server_script: Path,
) -> None:
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    retry_policy = OverloadRetryPolicy(
        max_attempts=2,
        base_delay_s=0.01,
        max_delay_s=0.05,
        jitter_s=0.0,
    )
    client = await _build_client(
        fake_codex_server_script,
        scenario="always_overload",
        sleep=fake_sleep,
        retry_policy=retry_policy,
    )
    try:
        with pytest.raises(CodexJsonRpcError) as exc_info:
            await client.request("thread/start", {"cwd": os.getcwd()})

        assert exc_info.value.code == -32001
        assert delays == [0.01]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stdio_client_eof_fails_pending_and_enqueues_disconnect(
    tmp_path: Path,
) -> None:
    """When the server process exits, pending futures fail and a disconnect event is enqueued."""
    script = tmp_path / "exit_after_init.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            import sys

            for raw in sys.stdin:
                raw = raw.strip()
                if not raw:
                    continue
                message = json.loads(raw)
                method = message.get("method")
                if method == "initialize":
                    sys.stdout.write(
                        json.dumps({"id": message["id"], "result": {"server": "fake"}})
                        + "\\n"
                    )
                    sys.stdout.flush()
                    continue
                if method == "initialized":
                    continue
                # Exit immediately on any other request (simulates crash).
                sys.exit(0)
            """
        ),
        encoding="utf-8",
    )
    client = CodexStdioClient(
        command=[sys.executable, "-u", str(script)],
    )
    await client.connect()
    await client.initialize(
        client_name="test",
        client_title="Test",
        client_version="0.0.1",
    )

    # Send a request that the server will never answer (it exits).
    with pytest.raises(RuntimeError, match="exited unexpectedly"):
        await client.request("thread/start", {"cwd": "/tmp"})

    # A transport/closed event should be available.
    event = await client.recv_event(timeout_s=1.0)
    assert event.method == "transport/closed"
    await client.close()
