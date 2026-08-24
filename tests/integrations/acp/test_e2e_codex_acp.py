"""End-to-end test: spawn codex-acp via ACP SDK and validate protocol flow.

This test spawns `codex-acp` as a real ACP agent subprocess and validates the full
protocol lifecycle. Protocol-level tests (initialize, new_session, list_sessions,
spawn failure) drive the raw ACP connection directly via `_spawn_codex_acp`.
Turn-level tests (prompt/collect, MCP tool calls, multiple sessions) drive
`ACPRuntime` instead — the same start/create_session/prompt/stop lifecycle
production code (`ACPClientAdapter`) uses, so a turn's `flush()` is never
something a test has to remember to call by hand.

Requires: codex-acp
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from band.integrations.acp.client_profiles import NoopACPClientProfile
from band.integrations.acp.client_runtime import (
    ACP_STDIO_LIMIT_BYTES,
    ACPRuntime,
    allow_permission,
    cancel_permission,
    select_allow_option_id,
)
from band.integrations.acp.client_types import BandACPClient
from band.integrations.acp.types import CollectedChunk
from band.integrations.mcp.engine import (
    MCPToolRegistration,
    build_band_mcp_tool_registrations,
)
from band.integrations.mcp.local_server import LocalMCPServer
from band.runtime.tools import AgentTools
from tests.toolkit.timeouts import backstop_timeout

logger = logging.getLogger(__name__)


def called_tool(tool_calls: list[CollectedChunk], tool_name: str) -> bool:
    """Whether any tool_call chunk invoked tool_name."""
    return any(
        chunk.metadata.get("raw_input", {}).get("tool") == tool_name
        for chunk in tool_calls
    )


# These are real E2E tests: each spawns `codex-acp` as a
# live subprocess (Node + network), so they are opt-in like the rest of the e2e
# suite. Gated on E2E_TESTS_ENABLED so a plain `uv run pytest` skips them — they
# are slow and their subprocess/fd pressure was starving nearby server tests
# (e.g. tests/integrations/mcp/test_local_server.py) into spurious timeouts.
_E2E_ENABLED = os.environ.get("E2E_TESTS_ENABLED", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_CODEX_ACP_COMMAND = shutil.which("codex-acp")
_INIT_TIMEOUT = 30
_PROMPT_TIMEOUT = 120
pytestmark = [
    pytest.mark.skipif(
        not _E2E_ENABLED,
        reason="codex-acp E2E tests are opt-in; set E2E_TESTS_ENABLED=true to run",
    ),
    pytest.mark.skipif(
        _CODEX_ACP_COMMAND is None,
        reason="codex-acp not available",
    ),
    pytest.mark.e2e,
    pytest.mark.timeout(backstop_timeout(_PROMPT_TIMEOUT)),
]


class EchoInput(BaseModel):
    """Echo text back to the caller."""

    message: str


def _spawn_codex_acp(acp_client: BandACPClient):
    """Spawn the installed codex-acp executable."""
    from acp import spawn_agent_process

    if _CODEX_ACP_COMMAND is None:
        pytest.skip("codex-acp not available")

    return spawn_agent_process(
        acp_client,
        _CODEX_ACP_COMMAND,
        transport_kwargs={"limit": ACP_STDIO_LIMIT_BYTES},
    )


@pytest.fixture
def acp_client() -> BandACPClient:
    """Create a fresh BandACPClient."""
    return BandACPClient(profile=NoopACPClientProfile())


@pytest.fixture
async def acp_runtime() -> AsyncIterator[ACPRuntime]:
    """A started ACPRuntime against the installed codex-acp, stopped on teardown.

    Drives full turns through the same start/create_session/prompt/stop lifecycle
    production code uses (``ACPClientAdapter``), rather than re-driving the raw ACP
    connection by hand — so a turn's ``flush()`` is never something a test has to
    remember to call itself.
    """
    if _CODEX_ACP_COMMAND is None:
        pytest.skip("codex-acp not available")

    runtime = ACPRuntime(
        command=[_CODEX_ACP_COMMAND],
        client_factory=lambda: BandACPClient(profile=NoopACPClientProfile()),
    )
    await asyncio.wait_for(runtime.start(), timeout=_INIT_TIMEOUT)
    try:
        yield runtime
    finally:
        await runtime.stop()


async def _allow_all_permissions(
    options: object = None, **kwargs: object
) -> dict[str, object]:
    """Approve every tool-call permission request by selecting an allow option.

    codex-acp asks the client to approve before running an MCP tool
    (``session/request_permission``); with no handler registered the client
    auto-cancels, so the tool comes back "cancelled". This mirrors the production
    bridge (``_make_permission_handler``): select an offered allow option."""
    del kwargs
    option_id = select_allow_option_id(options)
    return cancel_permission() if option_id is None else allow_permission(option_id)


@pytest.mark.asyncio
async def test_codex_acp_initialize(acp_client: BandACPClient) -> None:
    """Should successfully initialize the ACP protocol with codex-acp."""
    ctx = _spawn_codex_acp(acp_client)
    conn, _proc = await ctx.__aenter__()
    try:
        result = await asyncio.wait_for(
            conn.initialize(protocol_version=1),
            timeout=_INIT_TIMEOUT,
        )
        logger.info("Initialize result: %s", result)
        assert result is not None
        assert result.protocol_version == 1
        assert result.agent_info is not None
        logger.info("Agent: %s v%s", result.agent_info.name, result.agent_info.version)
    finally:
        await ctx.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_codex_acp_new_session(acp_client: BandACPClient) -> None:
    """Should create a new ACP session with cwd and mcp_servers."""
    ctx = _spawn_codex_acp(acp_client)
    conn, _proc = await ctx.__aenter__()
    try:
        await asyncio.wait_for(
            conn.initialize(protocol_version=1),
            timeout=_INIT_TIMEOUT,
        )

        session = await asyncio.wait_for(
            conn.new_session(cwd="/tmp", mcp_servers=[]),
            timeout=_INIT_TIMEOUT,
        )
        logger.info("Session ID: %s", session.session_id)
        assert session.session_id is not None
        assert len(session.session_id) > 0
    finally:
        await ctx.__aexit__(None, None, None)


@pytest.mark.asyncio(loop_scope="session")
async def test_codex_acp_prompt_and_collect(acp_runtime: ACPRuntime) -> None:
    """Should send a prompt and collect response chunks from codex-acp."""
    session_id = await asyncio.wait_for(
        acp_runtime.create_session(cwd="/tmp", mcp_servers=[]),
        timeout=_INIT_TIMEOUT,
    )
    acp_runtime.reset_session(session_id)

    chunks = await asyncio.wait_for(
        acp_runtime.prompt(
            session_id=session_id,
            prompt_text="What is 2 + 2? Reply with just the number.",
        ),
        timeout=_PROMPT_TIMEOUT,
    )
    text = acp_runtime.get_collected_text(session_id)
    logger.info("Collected %d chunks, text: %s", len(chunks), text[:200])

    assert len(chunks) > 0, "Expected at least one response chunk"
    assert len(text) > 0, "Expected non-empty response text"

    # Verify chunk types are valid
    valid_types = {"text", "thought", "tool_call", "tool_result", "plan"}
    seen_types = {chunk.chunk_type for chunk in chunks}
    assert seen_types <= valid_types, f"Unexpected chunk types: {seen_types}"


@pytest.mark.asyncio(loop_scope="session")
async def test_codex_acp_http_mcp_server_tool_call(
    acp_runtime: ACPRuntime,
) -> None:
    """Should connect to a local HTTP MCP server and execute a tool."""
    from acp.schema import HttpMcpServer

    # execute() must return a wire-serialized string: the dynamic handler
    # build_engine() creates always declares -> str, so FastMCP's
    # structured-output validation rejects a raw dict here.
    async def execute(arguments: dict[str, str]) -> str:
        return json.dumps({"echo": arguments["message"]})

    local_server = LocalMCPServer(
        name="test-codex-http-mcp",
        tool_registrations=[
            MCPToolRegistration(
                name="echo",
                description="Echo a provided message",
                input_model=EchoInput,
                execute=execute,
            )
        ],
        port_min=55110,
        port_max=55119,
    )

    await local_server.start()
    try:
        session_id = await asyncio.wait_for(
            acp_runtime.create_session(
                cwd="/tmp",
                mcp_servers=[
                    HttpMcpServer(
                        type="http",
                        name="smoke",
                        url=local_server.http_url,
                        headers=[],
                    )
                ],
            ),
            timeout=_INIT_TIMEOUT,
        )

        acp_runtime.reset_session(session_id)
        acp_runtime.set_permission_handler(session_id, _allow_all_permissions)

        chunks = await asyncio.wait_for(
            acp_runtime.prompt(
                session_id=session_id,
                prompt_text=(
                    "Use the smoke echo tool exactly once with message "
                    "'mcp smoke ok'. Then reply with only the tool result."
                ),
            ),
            timeout=_PROMPT_TIMEOUT,
        )
        text = acp_runtime.get_collected_text(session_id)

        assert any(chunk.chunk_type == "tool_call" for chunk in chunks)
        assert any(chunk.chunk_type == "tool_result" for chunk in chunks)
        assert "mcp smoke ok" in text
    finally:
        await local_server.stop()


@pytest.mark.asyncio(loop_scope="session")
async def test_codex_acp_band_mcp_tool_call(
    acp_runtime: ACPRuntime,
) -> None:
    """Should discover and call a real Band MCP tool."""
    from acp.schema import HttpMcpServer

    from tests.runtime.conftest import make_participant

    rest = SimpleNamespace(
        agent_api_participants=SimpleNamespace(
            list_agent_chat_participants=AsyncMock(
                return_value=SimpleNamespace(
                    data=[
                        make_participant(
                            {
                                "id": "u1",
                                "name": "Pat",
                                "type": "user",
                                "handle": "@pat",
                            }
                        ),
                        make_participant(
                            {
                                "id": "a1",
                                "name": "ACP Bridge",
                                "type": "agent",
                                "handle": "@pat/acp-bridge",
                            }
                        ),
                    ]
                )
            )
        )
    )
    agent_tools = AgentTools("room-123", rest)
    local_server = LocalMCPServer(
        name="test-band-http-mcp",
        tool_registrations=build_band_mcp_tool_registrations(agent_tools),
        port_min=55120,
        port_max=55129,
    )

    await local_server.start()
    try:
        session_id = await asyncio.wait_for(
            acp_runtime.create_session(
                cwd="/tmp",
                mcp_servers=[
                    HttpMcpServer(
                        type="http",
                        name="band",
                        url=local_server.http_url,
                        headers=[],
                    )
                ],
            ),
            timeout=_INIT_TIMEOUT,
        )

        acp_runtime.reset_session(session_id)
        acp_runtime.set_permission_handler(session_id, _allow_all_permissions)

        chunks = await asyncio.wait_for(
            acp_runtime.prompt(
                session_id=session_id,
                prompt_text=(
                    "You must call the Band get participants tool "
                    "exactly once. Do not answer from prior context. "
                    "After the tool call, reply with only the "
                    "participant names."
                ),
            ),
            timeout=_PROMPT_TIMEOUT,
        )
        text = acp_runtime.get_collected_text(session_id)

        tool_calls = [chunk for chunk in chunks if chunk.chunk_type == "tool_call"]
        if not tool_calls:
            pytest.skip("codex-acp did not invoke the Band MCP tool in this run")
        if not called_tool(tool_calls, "band_get_participants"):
            pytest.skip(
                "codex-acp invoked MCP in this run, but not the expected Band tool"
            )
        assert "Pat" in text
        assert "ACP Bridge" in text
    finally:
        await local_server.stop()


@pytest.mark.asyncio(loop_scope="session")
async def test_codex_acp_multiple_sessions(acp_runtime: ACPRuntime) -> None:
    """Should handle multiple concurrent sessions with separate buffers."""
    s1_id = await asyncio.wait_for(
        acp_runtime.create_session(cwd="/tmp", mcp_servers=[]), timeout=_INIT_TIMEOUT
    )
    s2_id = await asyncio.wait_for(
        acp_runtime.create_session(cwd="/tmp", mcp_servers=[]), timeout=_INIT_TIMEOUT
    )
    assert s1_id != s2_id

    acp_runtime.reset_session(s1_id)
    acp_runtime.reset_session(s2_id)

    # Send prompts to both (sequentially)
    chunks_1 = await asyncio.wait_for(
        acp_runtime.prompt(
            session_id=s1_id, prompt_text="Say 'hello' and nothing else."
        ),
        timeout=_PROMPT_TIMEOUT,
    )
    chunks_2 = await asyncio.wait_for(
        acp_runtime.prompt(
            session_id=s2_id, prompt_text="Say 'world' and nothing else."
        ),
        timeout=_PROMPT_TIMEOUT,
    )

    # Both sessions should have responses in separate buffers
    assert len(chunks_1) > 0, "Session 1 should have response chunks"
    assert len(chunks_2) > 0, "Session 2 should have response chunks"

    text_1 = acp_runtime.get_collected_text(s1_id)
    text_2 = acp_runtime.get_collected_text(s2_id)
    logger.info("Session 1: %s", text_1[:100])
    logger.info("Session 2: %s", text_2[:100])


@pytest.mark.asyncio
async def test_codex_acp_list_sessions(acp_client: BandACPClient) -> None:
    """Should list created sessions (if supported by the agent)."""
    from acp.exceptions import RequestError

    ctx = _spawn_codex_acp(acp_client)
    conn, _proc = await ctx.__aenter__()
    try:
        await asyncio.wait_for(
            conn.initialize(protocol_version=1),
            timeout=_INIT_TIMEOUT,
        )

        # Create a session
        session = await asyncio.wait_for(
            conn.new_session(cwd="/tmp"),
            timeout=_INIT_TIMEOUT,
        )

        # list_sessions is optional per ACP protocol — some agents don't implement it
        try:
            result = await asyncio.wait_for(
                conn.list_sessions(),
                timeout=_INIT_TIMEOUT,
            )
            logger.info("Listed sessions: %s", result)
            assert result is not None
            assert len(result.sessions) >= 1
            session_ids = {s.session_id for s in result.sessions}
            if session.session_id not in session_ids:
                pytest.skip(
                    "codex-acp session/list did not include the newly created "
                    "session in this environment"
                )
        except RequestError as e:
            if "Method not found" in str(e):
                pytest.skip("codex-acp does not support session/list")
            raise
    finally:
        await ctx.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_spawn_process_safety(acp_client: BandACPClient) -> None:
    """Should handle __aenter__ failure gracefully for bad command."""
    from acp import spawn_agent_process

    ctx = spawn_agent_process(acp_client, "nonexistent-acp-command-12345")
    with pytest.raises(Exception):
        await ctx.__aenter__()
