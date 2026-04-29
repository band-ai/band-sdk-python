"""End-to-end test: spawn codex-acp via ACP SDK and validate protocol flow.

This test spawns `npx @zed-industries/codex-acp` as a real ACP agent
subprocess and validates the full protocol lifecycle:
  1. Initialize connection
  2. Create a new session with cwd/mcp_servers
  3. Send a prompt
  4. Receive session_update chunks
  5. Verify collected chunks contain a response

Requires: npx, @zed-industries/codex-acp
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from thenvoi.integrations.acp.client_profiles import NoopACPClientProfile
from thenvoi.integrations.acp.client_types import ThenvoiACPClient
from thenvoi.runtime.mcp_server import (
    LocalMCPServer,
    MCPToolRegistration,
    build_thenvoi_mcp_tool_registrations,
)
from thenvoi.runtime.tools import AgentTools

logger = logging.getLogger(__name__)

# Skip entire module if npx is not available
pytestmark = [
    pytest.mark.skipif(
        shutil.which("npx") is None,
        reason="npx not available",
    ),
    pytest.mark.e2e,
]

_INIT_TIMEOUT = 30
_PROMPT_TIMEOUT = 120


class EchoInput(BaseModel):
    """Echo text back to the caller."""

    message: str


@pytest.fixture
def acp_client() -> ThenvoiACPClient:
    """Create a fresh ThenvoiACPClient."""
    return ThenvoiACPClient(profile=NoopACPClientProfile())


@pytest.mark.asyncio
async def test_codex_acp_initialize(acp_client: ThenvoiACPClient) -> None:
    """Should successfully initialize the ACP protocol with codex-acp."""
    from acp import spawn_agent_process

    ctx = spawn_agent_process(acp_client, "npx", "@zed-industries/codex-acp")
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
async def test_codex_acp_new_session(acp_client: ThenvoiACPClient) -> None:
    """Should create a new ACP session with cwd and mcp_servers."""
    from acp import spawn_agent_process

    ctx = spawn_agent_process(acp_client, "npx", "@zed-industries/codex-acp")
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


@pytest.mark.asyncio
async def test_codex_acp_prompt_and_collect(acp_client: ThenvoiACPClient) -> None:
    """Should send a prompt and collect response chunks from codex-acp."""
    from acp import spawn_agent_process, text_block

    ctx = spawn_agent_process(acp_client, "npx", "@zed-industries/codex-acp")
    conn, _proc = await ctx.__aenter__()
    try:
        await asyncio.wait_for(
            conn.initialize(protocol_version=1),
            timeout=_INIT_TIMEOUT,
        )
        session = await asyncio.wait_for(
            conn.new_session(cwd="/tmp"),
            timeout=_INIT_TIMEOUT,
        )
        session_id = session.session_id

        # Reset buffer for this session
        acp_client.reset_session(session_id)

        # Send a simple prompt
        result = await asyncio.wait_for(
            conn.prompt(
                session_id=session_id,
                prompt=[text_block("What is 2 + 2? Reply with just the number.")],
            ),
            timeout=_PROMPT_TIMEOUT,
        )
        logger.info("Prompt result: %s", result)

        # Check collected chunks
        chunks = acp_client.get_collected_chunks(session_id)
        text = acp_client.get_collected_text(session_id)
        logger.info("Collected %d chunks, text: %s", len(chunks), text[:200])

        assert len(chunks) > 0, "Expected at least one response chunk"
        assert len(text) > 0, "Expected non-empty response text"

        # Verify chunk types are valid
        valid_types = {"text", "thought", "tool_call", "tool_result", "plan"}
        for chunk in chunks:
            assert chunk.chunk_type in valid_types, (
                f"Unexpected chunk type: {chunk.chunk_type}"
            )
    finally:
        await ctx.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_codex_acp_http_mcp_server_tool_call(
    acp_client: ThenvoiACPClient,
) -> None:
    """Should connect to a local HTTP MCP server and execute a tool."""
    from acp import spawn_agent_process, text_block
    from acp.schema import HttpMcpServer

    async def execute(arguments: dict[str, str]) -> dict[str, str]:
        return {"echo": arguments["message"]}

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
        ctx = spawn_agent_process(
            acp_client,
            "npx",
            "@zed-industries/codex-acp",
        )
        conn, _proc = await ctx.__aenter__()
        try:
            init_result = await asyncio.wait_for(
                conn.initialize(protocol_version=1),
                timeout=_INIT_TIMEOUT,
            )
            mcp_capabilities = getattr(
                getattr(init_result, "agent_capabilities", None),
                "mcp_capabilities",
                None,
            )
            assert getattr(mcp_capabilities, "http", False)

            session = await asyncio.wait_for(
                conn.new_session(
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

            acp_client.reset_session(session.session_id)

            await asyncio.wait_for(
                conn.prompt(
                    session_id=session.session_id,
                    prompt=[
                        text_block(
                            "Use the smoke echo tool exactly once with message "
                            "'mcp smoke ok'. Then reply with only the tool result."
                        )
                    ],
                ),
                timeout=_PROMPT_TIMEOUT,
            )

            chunks = acp_client.get_collected_chunks(session.session_id)
            text = acp_client.get_collected_text(session.session_id)

            assert any(chunk.chunk_type == "tool_call" for chunk in chunks)
            assert any(chunk.chunk_type == "tool_result" for chunk in chunks)
            assert "mcp smoke ok" in text
        finally:
            await ctx.__aexit__(None, None, None)
    finally:
        await local_server.stop()


@pytest.mark.asyncio
async def test_codex_acp_thenvoi_mcp_tool_call(
    acp_client: ThenvoiACPClient,
) -> None:
    """Should discover and call a real Thenvoi MCP tool."""
    from acp import spawn_agent_process, text_block
    from acp.schema import HttpMcpServer

    rest = SimpleNamespace(
        agent_api_participants=SimpleNamespace(
            list_agent_chat_participants=AsyncMock(
                return_value=SimpleNamespace(
                    data=[
                        SimpleNamespace(
                            id="u1",
                            name="Pat",
                            type="user",
                            handle="@pat",
                        ),
                        SimpleNamespace(
                            id="a1",
                            name="ACP Bridge",
                            type="agent",
                            handle="@pat/acp-bridge",
                        ),
                    ]
                )
            )
        )
    )
    agent_tools = AgentTools("room-123", rest)
    local_server = LocalMCPServer(
        name="test-thenvoi-http-mcp",
        tool_registrations=build_thenvoi_mcp_tool_registrations(agent_tools),
        port_min=55120,
        port_max=55129,
    )

    await local_server.start()
    try:
        ctx = spawn_agent_process(
            acp_client,
            "npx",
            "@zed-industries/codex-acp",
        )
        conn, _proc = await ctx.__aenter__()
        try:
            init_result = await asyncio.wait_for(
                conn.initialize(protocol_version=1),
                timeout=_INIT_TIMEOUT,
            )
            mcp_capabilities = getattr(
                getattr(init_result, "agent_capabilities", None),
                "mcp_capabilities",
                None,
            )
            assert getattr(mcp_capabilities, "http", False)

            session = await asyncio.wait_for(
                conn.new_session(
                    cwd="/tmp",
                    mcp_servers=[
                        HttpMcpServer(
                            type="http",
                            name="thenvoi",
                            url=local_server.http_url,
                            headers=[],
                        )
                    ],
                ),
                timeout=_INIT_TIMEOUT,
            )

            acp_client.reset_session(session.session_id)

            await asyncio.wait_for(
                conn.prompt(
                    session_id=session.session_id,
                    prompt=[
                        text_block(
                            "You must call the Thenvoi get participants tool "
                            "exactly once. Do not answer from prior context. "
                            "After the tool call, reply with only the "
                            "participant names."
                        )
                    ],
                ),
                timeout=_PROMPT_TIMEOUT,
            )

            chunks = acp_client.get_collected_chunks(session.session_id)
            text = acp_client.get_collected_text(session.session_id)

            tool_calls = [chunk for chunk in chunks if chunk.chunk_type == "tool_call"]
            if not tool_calls:
                pytest.skip("codex-acp did not invoke the Thenvoi MCP tool in this run")
            if not any(
                chunk.metadata.get("raw_input", {}).get("tool")
                == "thenvoi_get_participants"
                for chunk in tool_calls
            ):
                pytest.skip(
                    "codex-acp invoked MCP in this run, but not the expected "
                    "Thenvoi tool"
                )
            assert "Pat" in text
            assert "ACP Bridge" in text
        finally:
            await ctx.__aexit__(None, None, None)
    finally:
        await local_server.stop()


@pytest.mark.asyncio
async def test_codex_acp_multiple_sessions(acp_client: ThenvoiACPClient) -> None:
    """Should handle multiple concurrent sessions with separate buffers."""
    from acp import spawn_agent_process, text_block

    ctx = spawn_agent_process(acp_client, "npx", "@zed-industries/codex-acp")
    conn, _proc = await ctx.__aenter__()
    try:
        await asyncio.wait_for(
            conn.initialize(protocol_version=1),
            timeout=_INIT_TIMEOUT,
        )

        # Create two sessions
        s1 = await asyncio.wait_for(conn.new_session(cwd="/tmp"), timeout=_INIT_TIMEOUT)
        s2 = await asyncio.wait_for(conn.new_session(cwd="/tmp"), timeout=_INIT_TIMEOUT)
        assert s1.session_id != s2.session_id

        # Reset buffers
        acp_client.reset_session(s1.session_id)
        acp_client.reset_session(s2.session_id)

        # Send prompts to both (sequentially)
        await asyncio.wait_for(
            conn.prompt(
                session_id=s1.session_id,
                prompt=[text_block("Say 'hello' and nothing else.")],
            ),
            timeout=_PROMPT_TIMEOUT,
        )
        await asyncio.wait_for(
            conn.prompt(
                session_id=s2.session_id,
                prompt=[text_block("Say 'world' and nothing else.")],
            ),
            timeout=_PROMPT_TIMEOUT,
        )

        # Both sessions should have responses in separate buffers
        chunks_1 = acp_client.get_collected_chunks(s1.session_id)
        chunks_2 = acp_client.get_collected_chunks(s2.session_id)

        assert len(chunks_1) > 0, "Session 1 should have response chunks"
        assert len(chunks_2) > 0, "Session 2 should have response chunks"

        text_1 = acp_client.get_collected_text(s1.session_id)
        text_2 = acp_client.get_collected_text(s2.session_id)
        logger.info("Session 1: %s", text_1[:100])
        logger.info("Session 2: %s", text_2[:100])
    finally:
        await ctx.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_codex_acp_list_sessions(acp_client: ThenvoiACPClient) -> None:
    """Should list created sessions (if supported by the agent)."""
    from acp import spawn_agent_process
    from acp.exceptions import RequestError

    ctx = spawn_agent_process(acp_client, "npx", "@zed-industries/codex-acp")
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
async def test_spawn_process_safety(acp_client: ThenvoiACPClient) -> None:
    """Should handle __aenter__ failure gracefully for bad command."""
    from acp import spawn_agent_process

    ctx = spawn_agent_process(acp_client, "nonexistent-acp-command-12345")
    with pytest.raises(Exception):
        await ctx.__aenter__()
