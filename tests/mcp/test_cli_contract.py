"""Subprocess-level contract tests for the published `band-mcp` CLI.

INT-1096 step 11: real ``band-mcp`` (via ``python -m band_mcp.server``)
subprocess, not an in-process call -- proves what a real MCP client actually
sees, including stdio stdout purity. Minimal on purpose (a handful of
configurations, not the plan's full agent-full/agent-pinned/human-full
battery) -- this exists to close a specific gap it already caught during
development: ``health_check`` is registered by ``run()`` itself, outside
``standalone_spec``, so the wire-schema snapshot test never covers it. A
more exhaustive subprocess contract suite (per-config schema/validation-text
parity) is still step 12's job, alongside the CLI package's release wiring.

Uses a syntactically-valid but fake credential: nothing here calls a tool
(only initialize/tools-list), so no network request ever happens.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys

import pytest

_BAND_CREDENTIAL_ENV_VARS = (
    "BAND_USER_KEY",
    "BAND_AGENT_KEY",
    "BAND_MCP_SCOPE",
    "BAND_MCP_TOOLS",
    "BAND_MCP_ROOM_ID",
)


def _clean_env(**overrides: str) -> dict[str, str]:
    """The ambient environment, minus any Band credential that would change
    which code path a test exercises, plus explicit overrides."""
    env = {k: v for k, v in os.environ.items() if k not in _BAND_CREDENTIAL_ENV_VARS}
    env.update(overrides)
    return env


_INIT_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "contract-test", "version": "0"},
    },
}
_INITIALIZED_NOTIFICATION = {"jsonrpc": "2.0", "method": "notifications/initialized"}
_LIST_TOOLS_REQUEST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


def _run_cli(*args: str, timeout: float = 15.0) -> tuple[int, str, str]:
    result = subprocess.run(
        [sys.executable, "-m", "band_mcp.server", *args],
        input="",
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_clean_env(),
    )
    return result.returncode, result.stdout, result.stderr


def test_version_flag() -> None:
    returncode, stdout, _ = _run_cli("--version")
    assert returncode == 0
    assert stdout.strip() == "band-mcp 1.3.2"


def test_help_flag_lists_flags() -> None:
    returncode, stdout, _ = _run_cli("--help")
    assert returncode == 0
    for flag in ("--user-key", "--agent-key", "--room-id", "--scope", "--tools"):
        assert flag in stdout


def test_missing_credential_exits_2_with_actionable_stderr() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "band_mcp.server"],
        input="",
        capture_output=True,
        text=True,
        timeout=10,
        env=_clean_env(),
    )
    assert result.returncode == 2
    assert "agent scope requested but no agent credential available" in result.stderr


async def _initialize_and_list_tools(*args: str) -> tuple[dict, dict, str]:
    """Speak just enough MCP over stdio to get tools/list back.

    Returns (initialize_result, tools_list_result, raw_stdout) so callers can
    assert on stdout purity directly.
    """
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "band_mcp.server",
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_clean_env(),
    )
    assert proc.stdin is not None
    assert proc.stdout is not None

    request = (
        json.dumps(_INIT_REQUEST)
        + "\n"
        + json.dumps(_INITIALIZED_NOTIFICATION)
        + "\n"
        + json.dumps(_LIST_TOOLS_REQUEST)
        + "\n"
    )
    proc.stdin.write(request.encode())
    await proc.stdin.drain()

    lines: list[str] = []
    try:
        while len(lines) < 2:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
            if not line:
                break
            lines.append(line.decode())
    finally:
        proc.stdin.close()
        proc.terminate()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=5)

    assert len(lines) == 2, f"expected 2 JSON-RPC frames on stdout, got: {lines!r}"
    init_result = json.loads(lines[0])["result"]
    tools_result = json.loads(lines[1])["result"]
    return init_result, tools_result, "".join(lines)


@pytest.mark.timeout(30)
async def test_stdio_agent_scope_advertises_health_check_with_correct_title() -> None:
    """Regression: health_check is registered by run() itself, outside
    standalone_spec -- a wrapper function named differently than the
    advertised tool leaks into the schema's auto-derived "title" field."""
    _, tools_result, raw_stdout = await _initialize_and_list_tools(
        "--agent-key", "band_a_x"
    )

    tools_by_name = {tool["name"]: tool for tool in tools_result["tools"]}
    assert "health_check" in tools_by_name
    assert (
        tools_by_name["health_check"]["inputSchema"]["title"] == "health_checkArguments"
    )

    # stdio stdout purity: every line must be a valid JSON-RPC frame -- no
    # stray log output interleaved (band_mcp.shared logs to stderr).
    for line in raw_stdout.splitlines():
        parsed = json.loads(line)
        assert parsed.get("jsonrpc") == "2.0"


@pytest.mark.timeout(30)
async def test_stdio_agent_scope_advertises_published_tool_names() -> None:
    _, tools_result, _ = await _initialize_and_list_tools("--agent-key", "band_a_x")

    names = {tool["name"] for tool in tools_result["tools"]}
    assert names == {
        "band_send_message",
        "band_send_event",
        "band_add_participant",
        "band_remove_participant",
        "band_lookup_peers",
        "band_get_participants",
        "band_create_chatroom",
        "health_check",
    }
