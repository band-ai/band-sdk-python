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

from collections.abc import AsyncGenerator
import asyncio
import json
import os
import socket
import sys
import uuid

import pytest
from pydantic import BaseModel
from band_rest import AsyncRestClient, ChatRoomRequest
from band_rest.types import ParticipantRequest

from band.agent import Agent
from band.core.types import AdapterFeatures, Emit

from tests.e2e.conftest import (
    E2ESettings,
    _assert_room_creation_budget_available,
    _track_created_room,
    requires_e2e,
)
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    message_value,
    run_smoke_test,
    send_trigger_message,
    wait_for_chat_messages,
)

try:
    import parlant.sdk as p

    HAS_PARLANT = True
except ImportError:
    HAS_PARLANT = False


class NativeEchoInput(BaseModel):
    """Echo back the validation code provided by the user."""

    code: str


def native_echo_handler(args: NativeEchoInput) -> dict:
    """Custom additional_tool used to exercise the wrapper's real-time emission."""
    return {"echo": f"verified-{args.code}"}


requires_parlant = pytest.mark.skipif(not HAS_PARLANT, reason="parlant not installed")


def _is_agent_text_message(payload, agent_id: str, expected_content: str) -> bool:
    return (
        message_value(payload, "message_type") == "text"
        and message_value(payload, "sender_type") == "Agent"
        and message_value(payload, "sender_id") == agent_id
        and expected_content in str(message_value(payload, "content") or "")
    )


def _message_timestamp_key(payload) -> str:
    timestamp = message_value(payload, "inserted_at") or message_value(
        payload, "created_at"
    )
    if timestamp is None:
        raise AssertionError("Chat message is missing inserted_at/created_at timestamp")
    return str(timestamp)


def _parlant_agent_credentials() -> tuple[str, str] | None:
    """Return local Parlant agent credentials without mutating process env."""
    current_key = os.getenv("BAND_API_KEY", "")
    test_agent_id = os.getenv("TEST_AGENT_ID")
    if current_key.startswith(("thnv_a", "band_a")) and test_agent_id:
        return test_agent_id, current_key

    try:
        from band.config import load_agent_config

        return load_agent_config("tom_agent")
    except (FileNotFoundError, ValueError):
        return None


@pytest.fixture(scope="session")
def e2e_config() -> E2ESettings:
    """Provide Parlant agent credentials without collection-time env mutation."""
    settings = E2ESettings()
    credentials = _parlant_agent_credentials()
    if credentials is None:
        return settings

    agent_id, api_key = credentials
    updates = {
        "band_api_key": api_key,
        "test_agent_id": agent_id,
    }
    current_key = os.getenv("BAND_API_KEY", "")
    if current_key.startswith(("thnv_u", "band_u")) and not settings.band_api_key_user:
        updates["band_api_key_user"] = current_key

    return settings.model_copy(update=updates)


def _unused_local_port() -> int:
    """Reserve a free localhost port for a short-lived in-process Parlant server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
async def e2e_parlant_room(
    e2e_session_client: AsyncRestClient,
    e2e_created_room_ids: list[str],
    e2e_room_creation_budget: int,
) -> tuple[str, str, str]:
    """Create a fresh Band room for each Parlant E2E test.

    Parlant E2E starts a new in-process Parlant server per test. Reusing a
    persistent Band room would hydrate stale prompts and responses into that new
    Parlant session, making LLM behavior depend on previous runs.
    """
    peers_response = await e2e_session_client.agent_api_peers.list_agent_peers(
        page_size=100,
    )
    user_peer = next((p for p in peers_response.data if p.type == "User"), None)
    if user_peer is None:
        pytest.skip("No User peer available for Parlant E2E tests")

    _assert_room_creation_budget_available(
        created_room_ids=e2e_created_room_ids,
        budget=e2e_room_creation_budget,
        label="parlant:fresh",
    )
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
    _track_created_room(
        created_room_ids=e2e_created_room_ids,
        budget=e2e_room_creation_budget,
        room_id=room_id,
        label="parlant:fresh",
    )
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
        from band.adapters.parlant import ParlantAdapter

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
            parlant_agent = await server.create_agent(
                name="E2E Test Agent",
                description=(
                    "A test agent for E2E validation. Keep responses short. "
                    "Incoming messages start with a mention to you; treat that as "
                    "the trigger target, not the reply recipient. Reply to the "
                    "user who sent the message."
                ),
            )
            # Steering guidelines only. Band/built-in tools and the custom
            # `nativeecho` tool are registered by the adapter's contract
            # guideline via ``create_parlant_tools(additional_tools=...)``.
            await parlant_agent.create_guideline(
                condition="User asks you to reply with a specific word or phrase",
                action=(
                    "Reply to the user with the requested word or phrase as the "
                    "message content. Do not address or mention yourself."
                ),
            )
            await parlant_agent.create_guideline(
                condition="User asks for echo validation with a code",
                action=(
                    "Use the native echo tool with the exact validation code. "
                    "Then reply to the user with content containing the returned "
                    "echo code, not with a message addressed to yourself."
                ),
            )

            adapter = ParlantAdapter(
                server=server,
                parlant_agent=parlant_agent,
                custom_section="Keep responses short and concise.",
                features=AdapterFeatures(emit={Emit.EXECUTION}),
                additional_tools=[(NativeEchoInput, native_echo_handler)],
            )

            agent = Agent.create(
                adapter=adapter,
                agent_id=e2e_config.test_agent_id,
                api_key=e2e_config.band_api_key,
                ws_url=e2e_config.band_ws_url,
                rest_url=e2e_config.band_base_url,
            )

            async with agent:
                yield agent
        finally:
            body_exc = sys.exc_info()[1]
            try:
                await asyncio.wait_for(server.__aexit__(None, None, None), timeout=30)
            except Exception as cleanup_exc:
                message = (
                    "Failed to stop the in-process Parlant E2E server. "
                    "Teardown failures can leak listeners into later tests."
                )
                if body_exc is not None:
                    if hasattr(body_exc, "add_note"):
                        body_exc.add_note(f"{message} Cleanup error: {cleanup_exc!r}")
                else:
                    raise AssertionError(message) from cleanup_exc

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
        """Verify the agent sends a visible chat response."""
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
        received = await wait_for_chat_messages(
            api_client,
            chat_id,
            lambda messages: any(
                _is_agent_text_message(msg, agent_id, token) for msg in messages
            ),
            e2e_config.e2e_timeout,
        )

        assert any(_is_agent_text_message(msg, agent_id, token) for msg in received)

    async def test_execution_emit_reports_additional_custom_tool(
        self,
        e2e_config: E2ESettings,
        e2e_parlant_room: tuple[str, str, str],
        e2e_agent_info: tuple[str, str],
        ws_client: TrackingWebSocketClient,
        running_parlant_agent: Agent,
        api_client: AsyncRestClient,
    ):
        """Emit.EXECUTION reports adapter-registered tools (built-ins + additional_tools)
        in real time, with tool_call ordered before tool_result and before the reply.

        Raw Parlant tools attached directly to a guideline outside additional_tools
        are intentionally not reported by the wrapper-based path.
        """
        chat_id, _user_id, _user_name = e2e_parlant_room
        agent_id, agent_name = e2e_agent_info
        code = f"NATIVE-{uuid.uuid4().hex[:8]}"
        expected_echo = f"verified-{code}"

        def has_expected_messages(messages) -> bool:
            has_tool_call = any(
                message_value(msg, "message_type") == "tool_call"
                and "nativeecho" in str(message_value(msg, "content") or "")
                and code in str(message_value(msg, "content") or "")
                for msg in messages
            )
            has_tool_result = any(
                message_value(msg, "message_type") == "tool_result"
                and "nativeecho" in str(message_value(msg, "content") or "")
                and code in str(message_value(msg, "content") or "")
                for msg in messages
            )
            has_text_reply = any(
                _is_agent_text_message(msg, agent_id, expected_echo) for msg in messages
            )
            return has_tool_call and has_tool_result and has_text_reply

        await send_trigger_message(
            api_client,
            chat_id,
            f"Echo validation: call the nativeecho tool with code {code}, then reply to the user with the returned echo value.",
            agent_name,
            agent_id,
        )
        received = await wait_for_chat_messages(
            api_client,
            chat_id,
            has_expected_messages,
            e2e_config.e2e_timeout,
        )

        tool_call = next(
            msg
            for msg in received
            if message_value(msg, "message_type") == "tool_call"
            and "nativeecho" in str(message_value(msg, "content") or "")
            and code in str(message_value(msg, "content") or "")
        )
        tool_result = next(
            msg
            for msg in received
            if message_value(msg, "message_type") == "tool_result"
            and "nativeecho" in str(message_value(msg, "content") or "")
            and code in str(message_value(msg, "content") or "")
        )
        text_reply = next(
            msg
            for msg in received
            if _is_agent_text_message(msg, agent_id, expected_echo)
        )
        ordered_messages = sorted(received, key=_message_timestamp_key)
        assert ordered_messages.index(tool_call) < ordered_messages.index(tool_result)
        assert ordered_messages.index(tool_result) < ordered_messages.index(text_reply)

        call_payload = json.loads(message_value(tool_call, "content"))
        result_payload = json.loads(message_value(tool_result, "content"))
        assert call_payload["name"] == "nativeecho"
        assert call_payload["args"]["code"] == code
        assert result_payload["name"] == "nativeecho"
        assert result_payload["output"]["echo"] == expected_echo
        # tool_call and tool_result are correlated by a stable id emitted by the
        # wrapper around a single execute_custom_tool invocation.
        assert call_payload["tool_call_id"] == result_payload["tool_call_id"]
