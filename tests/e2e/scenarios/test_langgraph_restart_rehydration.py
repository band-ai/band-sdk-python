"""Live LangGraph restart rehydration smoke.

Run manually only:
    E2E_TESTS_ENABLED=true LANGGRAPH_RESTART_SMOKE=true uv run pytest \
        tests/e2e/scenarios/test_langgraph_restart_rehydration.py -v -s \
        --log-cli-level=INFO --no-cov
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from band_rest import (
    AgentRegisterRequest,
    AsyncRestClient,
    ChatMessageRequest,
    ChatRoomRequest,
)
from band_rest.types import (
    ChatMessageRequestMentionsItem as Mention,
    ParticipantRequest,
)

from band import Agent
from band.adapters import LangGraphAdapter
from band.client.streaming import MessageCreatedPayload, WebSocketClient
from tests.e2e.conftest import requires_e2e, requires_openai


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TemporaryAgent:
    agent_id: str
    api_key: str
    name: str


@dataclass(frozen=True)
class AgentMessageObserver:
    received: list[MessageCreatedPayload]
    ready: asyncio.Event


@asynccontextmanager
async def _agent_message_observer(
    ws: WebSocketClient,
    room_id: str,
    agent_id: str,
) -> AsyncIterator[AgentMessageObserver]:
    received: list[MessageCreatedPayload] = []
    ready = asyncio.Event()

    async def on_message(payload: MessageCreatedPayload) -> None:
        if (
            payload.sender_type == "Agent"
            and payload.sender_id == agent_id
            and payload.message_type == "text"
        ):
            received.append(payload)
            ready.set()

    await ws.join_chat_room_channel(room_id, on_message)
    try:
        yield AgentMessageObserver(received=received, ready=ready)
    finally:
        await ws.leave_chat_room_channel(room_id)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required for LangGraph restart smoke")
    return value


def _make_adapter() -> LangGraphAdapter:
    return LangGraphAdapter(
        llm=ChatOpenAI(model=os.environ.get("E2E_LLM_MODEL", "gpt-4o-mini")),
        checkpointer=InMemorySaver(),
        custom_section=(
            "Keep responses short. Always reply by calling band_send_message. "
            "If asked what nonce to remember, answer with exactly that nonce."
        ),
    )


async def _register_temporary_agent(user_client: AsyncRestClient) -> TemporaryAgent:
    suffix = uuid.uuid4().hex[:8]
    response = await user_client.human_api_agents.register_my_agent(
        agent=AgentRegisterRequest(
            name=f"LangGraph Restart Smoke {suffix}",
            description="Temporary LangGraph restart rehydration smoke agent.",
        )
    )
    data = response.data
    return TemporaryAgent(
        agent_id=data.agent.id,
        api_key=data.credentials.api_key,
        name=data.agent.name,
    )


async def _create_room_with_owner(agent_client: AsyncRestClient) -> tuple[str, str]:
    peers_response = await agent_client.agent_api_peers.list_agent_peers()
    user_peer = next((p for p in peers_response.data or [] if p.type == "User"), None)
    if user_peer is None:
        pytest.fail("No owner User peer available for temporary agent")

    chat_response = await agent_client.agent_api_chats.create_agent_chat(
        chat=ChatRoomRequest()
    )
    room_id = chat_response.data.id
    await agent_client.agent_api_participants.add_agent_chat_participant(
        room_id,
        participant=ParticipantRequest(participant_id=user_peer.id, role="member"),
    )
    return room_id, user_peer.id


async def _send_user_message(
    user_client: AsyncRestClient,
    room_id: str,
    agent: TemporaryAgent,
    content: str,
) -> str:
    response = await user_client.human_api_messages.send_my_chat_message(
        room_id,
        message=ChatMessageRequest(
            content=f"@{agent.name} {content}",
            mentions=[Mention(id=agent.agent_id, name=agent.name)],
        ),
    )
    return response.data.id


async def _wait_for_agent_messages(
    observer: AgentMessageObserver,
    *,
    min_messages: int,
    timeout: float,
    quiet_after_first: float = 0,
) -> list[MessageCreatedPayload]:
    while len(observer.received) < min_messages:
        observer.ready.clear()
        if len(observer.received) >= min_messages:
            break
        await asyncio.wait_for(observer.ready.wait(), timeout=timeout)

    if quiet_after_first:
        await asyncio.sleep(quiet_after_first)
    return list(observer.received)


@pytest.mark.asyncio
@requires_e2e
@requires_openai
@pytest.mark.skipif(
    os.environ.get("LANGGRAPH_RESTART_SMOKE", "").lower() != "true",
    reason="LANGGRAPH_RESTART_SMOKE=true is required for the live restart smoke",
)
async def test_langgraph_answers_down_message_once_after_restart() -> None:
    base_url = os.environ.get("BAND_BASE_URL") or os.environ.get("BAND_REST_URL")
    if not base_url:
        pytest.skip("BAND_BASE_URL or BAND_REST_URL is required")
    ws_url = _require_env("BAND_WS_URL")
    user_key = os.environ.get("BAND_API_KEY_USER") or os.environ.get(
        "BAND_USER_API_KEY"
    )
    if not user_key:
        pytest.skip("BAND_API_KEY_USER or BAND_USER_API_KEY is required")

    nonce = f"REHYDRATE_{uuid.uuid4().hex[:12]}"
    user_client = AsyncRestClient(api_key=user_key, base_url=base_url)
    agent: TemporaryAgent | None = None

    try:
        agent = await _register_temporary_agent(user_client)
        agent_client = AsyncRestClient(api_key=agent.api_key, base_url=base_url)
        room_id, _owner_user_id = await _create_room_with_owner(agent_client)

        ws = WebSocketClient(ws_url=ws_url, api_key=user_key, agent_id=None)
        async with ws:
            first_agent = Agent.create(
                adapter=_make_adapter(),
                agent_id=agent.agent_id,
                api_key=agent.api_key,
                ws_url=ws_url,
                rest_url=base_url,
            )
            async with first_agent:
                async with _agent_message_observer(
                    ws, room_id, agent.agent_id
                ) as observer:
                    await _send_user_message(
                        user_client,
                        room_id,
                        agent,
                        f"Remember this nonce: {nonce}. Reply that you will remember it.",
                    )
                    first_responses = await _wait_for_agent_messages(
                        observer,
                        min_messages=1,
                        timeout=45,
                    )
                    assert len(first_responses) == 1
                    assert nonce.lower() in first_responses[0].content.lower()

            down_message_id = await _send_user_message(
                user_client,
                room_id,
                agent,
                "What nonce did I ask you to remember? Reply with just the nonce.",
            )

            async with _agent_message_observer(ws, room_id, agent.agent_id) as observer:
                restarted_agent = Agent.create(
                    adapter=_make_adapter(),
                    agent_id=agent.agent_id,
                    api_key=agent.api_key,
                    ws_url=ws_url,
                    rest_url=base_url,
                )
                async with restarted_agent:
                    restart_responses = await _wait_for_agent_messages(
                        observer,
                        min_messages=1,
                        timeout=60,
                        quiet_after_first=12,
                    )

            restart_contents = [r.content for r in restart_responses]
            assert len(restart_responses) == 1, restart_contents
            assert nonce.lower() in restart_responses[0].content.lower()

            async with _agent_message_observer(ws, room_id, agent.agent_id) as observer:
                second_restart_agent = Agent.create(
                    adapter=_make_adapter(),
                    agent_id=agent.agent_id,
                    api_key=agent.api_key,
                    ws_url=ws_url,
                    rest_url=base_url,
                )
                async with second_restart_agent:
                    await asyncio.sleep(10)
                    quiet_responses = list(observer.received)

            assert quiet_responses == []
            logger.info(
                "RESULT langgraph_restart_rehydration=PASS "
                "down_message_id=%s nonce_prefix=%s",
                down_message_id,
                nonce[:18],
            )
            logger.info(
                "ASSERTIONS no_replay_burst=True "
                "pending_down_message_answered_once=True "
                "recalled_pre_restart_context=True "
                "second_restart_no_new_reply=True"
            )
    finally:
        if agent is not None:
            try:
                await user_client.human_api_agents.delete_my_agent(
                    agent.agent_id,
                    force=True,
                )
            except Exception:
                logger.exception("Failed to delete temporary LangGraph smoke agent")
