"""E2E tests for the Parlant adapter.

Verifies that the Parlant adapter can:
- Start, process a message, and stop against a real platform
- Execute platform tools (send_message)

Note: Parlant requires a running Parlant server. These tests create
a server + agent in-process using the Parlant SDK.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/adapters/test_parlant.py -v -s --no-cov
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
import asyncio
import contextlib
import inspect
import json
import os
import socket
import uuid

import pytest
from thenvoi_rest import AsyncRestClient, ChatRoomRequest
from thenvoi_rest.types import ParticipantRequest

from thenvoi.agent import Agent
from thenvoi.core.types import AdapterFeatures, Emit

from tests.e2e.conftest import E2ESettings, requires_e2e
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    run_smoke_test,
    send_trigger_message,
)

try:
    import parlant.sdk as p

    HAS_PARLANT = True
except ImportError:
    HAS_PARLANT = False

requires_parlant = pytest.mark.skipif(not HAS_PARLANT, reason="parlant not installed")


def _message_value(payload, key: str):
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


async def _wait_for_chat_messages(
    client: AsyncRestClient,
    chat_id: str,
    predicate: Callable[[list], bool],
    timeout: float,
):
    """Poll the durable human-visible room history until the expected messages exist."""
    deadline = asyncio.get_running_loop().time() + timeout
    last_messages = []
    while asyncio.get_running_loop().time() < deadline:
        response = await client.human_api_messages.list_my_chat_messages(
            chat_id,
            page_size=50,
        )
        last_messages = list(response.data or [])
        if predicate(last_messages):
            return last_messages
        await asyncio.sleep(0.5)

    summary = [
        {
            "type": _message_value(msg, "message_type"),
            "sender": _message_value(msg, "sender_name"),
            "content": str(_message_value(msg, "content") or "")[:160],
        }
        for msg in last_messages[:12]
    ]
    raise TimeoutError(f"Timed out waiting for expected Parlant messages: {summary}")


def _configure_parlant_agent_credentials() -> None:
    """Use the local Parlant agent config when a sourced env exposes a user key."""
    current_key = os.getenv("THENVOI_API_KEY", "")
    if current_key.startswith(("thnv_a", "band_a")) and os.getenv("TEST_AGENT_ID"):
        return

    if current_key.startswith(("thnv_u", "band_u")) and not os.getenv(
        "THENVOI_API_KEY_USER"
    ):
        os.environ["THENVOI_API_KEY_USER"] = current_key

    try:
        from thenvoi.config import load_agent_config

        agent_id, api_key = load_agent_config("tom_agent")
    except (FileNotFoundError, ValueError):
        return

    os.environ["THENVOI_API_KEY"] = api_key
    os.environ["TEST_AGENT_ID"] = agent_id
    os.environ.setdefault("THENVOI_AGENT_ID", agent_id)


_configure_parlant_agent_credentials()


def _unused_local_port() -> int:
    """Reserve a free localhost port for a short-lived in-process Parlant server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
async def e2e_parlant_room(
    e2e_session_client: AsyncRestClient,
    e2e_created_room_ids: list[str],
) -> tuple[str, str, str]:
    """Create a fresh Band room for each Parlant E2E test.

    Parlant E2E starts a new in-process Parlant server per test. Reusing a
    persistent Band room would hydrate stale prompts and responses into that new
    Parlant session, making LLM behavior depend on previous runs.
    """
    peers_response = await e2e_session_client.agent_api_peers.list_agent_peers()
    user_peer = next((p for p in peers_response.data if p.type == "User"), None)
    if user_peer is None:
        pytest.skip("No User peer available for Parlant E2E tests")

    response = await e2e_session_client.agent_api_chats.create_agent_chat(
        chat=ChatRoomRequest()
    )
    if response.data is None:
        pytest.fail("create_agent_chat returned no data")
    room_id = response.data.id
    await e2e_session_client.agent_api_participants.add_agent_chat_participant(
        room_id,
        participant=ParticipantRequest(participant_id=user_peer.id, role="member"),
    )
    e2e_created_room_ids.append(room_id)
    return room_id, user_peer.id, user_peer.name


@pytest.mark.asyncio
@requires_e2e
@requires_parlant
class TestParlantE2E:
    """E2E tests specific to the Parlant adapter.

    These tests require Parlant to be installed and create an in-process
    Parlant server for each test.
    """

    @pytest.fixture
    async def running_parlant_agent(
        self,
        e2e_config: E2ESettings,
    ) -> AsyncGenerator[Agent, None]:
        """Create a Parlant adapter with an in-process server and start the agent.

        Yields a running Agent inside its async context manager.
        """
        from thenvoi.adapters.parlant import ParlantAdapter
        from thenvoi.integrations.parlant.tools import create_parlant_tools

        if not os.getenv("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY is required for Parlant E2E tests")

        server = p.Server(
            host="127.0.0.1",
            port=_unused_local_port(),
            tool_service_port=_unused_local_port(),
            nlp_service=p.NLPServices.openai,
        )
        await server.__aenter__()
        try:
            from parlant.core.tools import ToolContext, ToolResult

            async def native_echo_impl(context, code):
                """Return the exact validation code provided by the user."""
                return ToolResult(data={"echo": code})

            native_echo_impl.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
                parameters=[
                    inspect.Parameter(
                        "context",
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        annotation=ToolContext,
                    ),
                    inspect.Parameter(
                        "code",
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        annotation=str,
                    ),
                ],
                return_annotation=ToolResult,
            )
            native_echo = p.tool(name="native_echo")(native_echo_impl)

            parlant_tools = create_parlant_tools()
            parlant_agent = await server.create_agent(
                name="E2E Test Agent",
                description=(
                    "A test agent for E2E validation. Keep responses short. "
                    "Incoming messages start with a mention to you; treat that as "
                    "the trigger target, not the reply recipient. Reply to the "
                    "user who sent the message."
                ),
            )
            await parlant_agent.create_guideline(
                condition="User asks you to reply with a specific word or phrase",
                action=(
                    "Call thenvoi_send_message with the requested word or phrase "
                    "as content, and set mentions to the user's name or handle. "
                    "Do not address or mention yourself."
                ),
                tools=parlant_tools,
            )
            await parlant_agent.create_guideline(
                condition="User asks for native echo validation",
                action=(
                    "Call native_echo with the exact validation code. Then call "
                    "thenvoi_send_message with content containing the returned echo "
                    "code, and mention the user, not yourself."
                ),
                tools=[native_echo, *parlant_tools],
            )

            adapter = ParlantAdapter(
                server=server,
                parlant_agent=parlant_agent,
                custom_section="Keep responses short and concise.",
                features=AdapterFeatures(emit={Emit.EXECUTION}),
            )

            agent = Agent.create(
                adapter=adapter,
                agent_id=e2e_config.test_agent_id,
                api_key=e2e_config.thenvoi_api_key,
                ws_url=e2e_config.thenvoi_ws_url,
                rest_url=e2e_config.thenvoi_base_url,
            )

            async with agent:
                yield agent
        finally:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(server.__aexit__(None, None, None), timeout=30)

    async def test_smoke_responds_to_message(
        self,
        e2e_config: E2ESettings,
        e2e_parlant_room: tuple[str, str, str],
        e2e_agent_info: tuple[str, str],
        ws_client: TrackingWebSocketClient,
        running_parlant_agent: Agent,
        api_client: AsyncRestClient,
    ):
        """Smoke test: agent starts, receives a message, and responds."""
        chat_id, _user_id, _user_name = e2e_parlant_room
        agent_id, agent_name = e2e_agent_info

        await run_smoke_test(
            ws_client,
            api_client,
            chat_id,
            agent_name,
            agent_id,
            timeout=e2e_config.e2e_timeout,
            adapter_name="parlant",
        )

    async def test_tool_execution_send_message(
        self,
        e2e_config: E2ESettings,
        e2e_parlant_room: tuple[str, str, str],
        e2e_agent_info: tuple[str, str],
        ws_client: TrackingWebSocketClient,
        running_parlant_agent: Agent,
        api_client: AsyncRestClient,
    ):
        """Verify the agent uses thenvoi_send_message tool to respond."""
        chat_id, _user_id, user_name = e2e_parlant_room
        agent_id, agent_name = e2e_agent_info
        token = f"PINEAPPLE-{uuid.uuid4().hex[:8]}"
        await send_trigger_message(
            api_client,
            chat_id,
            f"Reply to {user_name} with the exact phrase {token}. Do not reply to {agent_name}.",
            agent_name,
            agent_id,
        )
        received = await _wait_for_chat_messages(
            api_client,
            chat_id,
            lambda messages: any(
                _message_value(msg, "message_type") == "text"
                and token in str(_message_value(msg, "content") or "")
                for msg in messages
            ),
            e2e_config.e2e_timeout,
        )

        assert any(
            token in str(_message_value(msg, "content") or "") for msg in received
        )

    async def test_execution_emit_reports_native_parlant_tool(
        self,
        e2e_config: E2ESettings,
        e2e_parlant_room: tuple[str, str, str],
        e2e_agent_info: tuple[str, str],
        ws_client: TrackingWebSocketClient,
        running_parlant_agent: Agent,
        api_client: AsyncRestClient,
    ):
        """Verify Emit.EXECUTION reports native Parlant tools, not only SDK wrappers."""
        chat_id, _user_id, _user_name = e2e_parlant_room
        agent_id, agent_name = e2e_agent_info
        code = f"NATIVE-{uuid.uuid4().hex[:8]}"

        def has_expected_messages(messages) -> bool:
            has_native_tool_call = any(
                _message_value(msg, "message_type") == "tool_call"
                and "native_echo" in str(_message_value(msg, "content") or "")
                and code in str(_message_value(msg, "content") or "")
                for msg in messages
            )
            has_native_tool_result = any(
                _message_value(msg, "message_type") == "tool_result"
                and code in str(_message_value(msg, "content") or "")
                for msg in messages
            )
            has_text_reply = any(
                _message_value(msg, "message_type") == "text"
                and code in str(_message_value(msg, "content") or "")
                for msg in messages
            )
            return has_native_tool_call and has_native_tool_result and has_text_reply

        await send_trigger_message(
            api_client,
            chat_id,
            f"Native echo validation: call native_echo with code {code}, then reply to the user with that exact code.",
            agent_name,
            agent_id,
        )
        received = await _wait_for_chat_messages(
            api_client,
            chat_id,
            has_expected_messages,
            e2e_config.e2e_timeout,
        )

        tool_call = next(
            msg
            for msg in received
            if _message_value(msg, "message_type") == "tool_call"
            and "native_echo" in str(_message_value(msg, "content") or "")
            and code in str(_message_value(msg, "content") or "")
        )
        tool_result = next(
            msg
            for msg in received
            if _message_value(msg, "message_type") == "tool_result"
            and code in str(_message_value(msg, "content") or "")
        )
        call_payload = json.loads(_message_value(tool_call, "content"))
        result_payload = json.loads(_message_value(tool_result, "content"))
        assert call_payload["name"] == "native_echo"
        assert call_payload["args"]["code"] == code
        assert result_payload["name"] == "native_echo"
        assert result_payload["output"]["echo"] == code
