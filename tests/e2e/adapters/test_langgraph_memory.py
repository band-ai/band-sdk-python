"""E2E skeleton for LangGraph memory tool usage.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/adapters/test_langgraph_memory.py -v -s --no-cov
"""

from __future__ import annotations

import os
import asyncio
from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
from thenvoi_rest import AsyncRestClient, ChatRoomRequest
from thenvoi_rest.types import ParticipantRequest

from band import Agent
from band.adapters.langgraph import LangGraphAdapter
from band.core.types import AdapterFeatures, Capability
from tests.e2e.conftest import E2ESettings, requires_e2e
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    listening_for_agent_responses,
    send_trigger_message,
)

MEMORY_CUSTOM_SECTION = (
    "Actively look for durable information worth remembering. "
    "When a user states a preference, profile detail, standing instruction, "
    "important project fact, or reusable workflow, call `band_store_memory` "
    "before replying. Use memory sparingly: do not store one-off requests, "
    "temporary chat context, or sensitive information unless the user clearly "
    "asks you to remember it. After storing a memory, briefly acknowledge what "
    "you saved and continue helping the user."
)


@pytest.fixture
async def langgraph_memory_room(
    running_langgraph_memory_agent: Agent,
    e2e_config: E2ESettings,
    e2e_created_room_ids: list[str],
) -> tuple[str, str, str]:
    """Create a fresh room after the agent is running.

    Creating the room after startup forces the live agent to receive a
    ``room_added`` event and avoids stale reused room subscriptions.
    """
    client = AsyncRestClient(
        api_key=e2e_config.band_api_key,
        base_url=e2e_config.band_base_url,
    )

    peers_response = await client.agent_api_peers.list_agent_peers()
    user_peer = next((p for p in peers_response.data if p.type == "User"), None)
    if user_peer is None:
        pytest.skip("No User peer available for E2E tests")

    response = await client.agent_api_chats.create_agent_chat(chat=ChatRoomRequest())
    if response.data is None:
        pytest.fail("create_agent_chat returned no data")

    room_id = response.data.id
    e2e_created_room_ids.append(room_id)

    await client.agent_api_participants.add_agent_chat_participant(
        room_id,
        participant=ParticipantRequest(participant_id=user_peer.id, role="member"),
    )

    # Give the running agent a short window to receive room_added and subscribe
    # before the trigger message is sent.
    await asyncio.sleep(1)
    return room_id, user_peer.id, user_peer.name


@pytest.fixture
async def running_langgraph_memory_agent(
    e2e_config: E2ESettings,
) -> AsyncGenerator[Agent, None]:
    """Run a LangGraph agent configured with Band memory tools enabled."""
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import MemorySaver

    adapter = LangGraphAdapter(
        llm=ChatOpenAI(model=e2e_config.e2e_llm_model),
        checkpointer=MemorySaver(),
        custom_section=MEMORY_CUSTOM_SECTION,
        features=AdapterFeatures(capabilities={Capability.MEMORY}),
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


async def _list_memories_containing(
    agent_client: AsyncRestClient,
    marker: str,
    subject_ids: list[str],
) -> list[object]:
    """Return active memories whose content contains ``marker``.

    A ``subject_id`` query returns only memories about that subject, not
    organization-wide ones, so we query organization scope (where the agent
    stores memories it can't attribute to a subject UUID) plus each candidate
    subject and union the results. We match by substring client-side because the
    underscore marker isn't reliably tokenized by the server-side full-text
    ``content_query`` filter.
    """
    queries: list[dict[str, object]] = [
        {"page_size": 50, "status": "active", "scope": "organization"},
        *(
            {"page_size": 50, "status": "active", "subject_id": subject_id}
            for subject_id in subject_ids
        ),
    ]

    matches: list[object] = []
    seen: set[str] = set()
    for query in queries:
        response = await agent_client.agent_api_memories.list_agent_memories(**query)
        for memory in response.data or []:
            mem_id = getattr(memory, "id", None)
            if mem_id not in seen and marker in (
                getattr(memory, "content", None) or ""
            ):
                seen.add(mem_id)
                matches.append(memory)
    return matches


async def _wait_for_memories_containing(
    agent_client: AsyncRestClient,
    marker: str,
    subject_ids: list[str],
    *,
    timeout: float = 7.0,
    interval: float = 5.0,
) -> list[object]:
    """Poll memories until one containing ``marker`` appears or timeout expires."""
    deadline = asyncio.get_running_loop().time() + timeout
    last_result: list[object] = []

    while asyncio.get_running_loop().time() < deadline:
        last_result = await _list_memories_containing(agent_client, marker, subject_ids)
        if last_result:
            return last_result
        await asyncio.sleep(interval)

    return last_result


# loop_scope="session" pins the test to the same event loop as the agent's
# background processing task, so the agent processes the trigger concurrently with
# the test body. A bare @pytest.mark.asyncio would run the body on a separate loop
# and the agent couldn't process the message until fixture teardown.
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.flaky(reruns=2)
@requires_e2e
async def test_langgraph_agent_stores_durable_user_memory(
    e2e_config: E2ESettings,
    langgraph_memory_room: tuple[str, str, str],
    e2e_agent_info: tuple[str, str],
    running_langgraph_memory_agent: Agent,
    ws_client: TrackingWebSocketClient,
) -> None:
    """Ask LangGraph to remember a durable preference and verify it is stored."""
    chat_id, user_id, _user_name = langgraph_memory_room
    agent_id, agent_name = e2e_agent_info
    marker = f"LANGGRAPH_MEMORY_E2E_{uuid4().hex}"
    user_client = AsyncRestClient(
        api_key=e2e_config.band_api_key_user, base_url=e2e_config.band_base_url
    )
    agent_client = AsyncRestClient(
        api_key=e2e_config.band_api_key, base_url=e2e_config.band_base_url
    )

    prompt = (
        "Remember this durable preference exactly: "
        f"{marker} means I prefer concise memory test responses. "
        "Store it as a long-term semantic user memory, then acknowledge it briefly."
    )

    # Wait for the agent's reply, which proves it processed the message (and had
    # the chance to call band_store_memory) before we poll for the memory.
    async with listening_for_agent_responses(
        ws_client, chat_id, timeout=e2e_config.e2e_timeout, raise_on_timeout=True
    ) as wait_for_reply:
        await send_trigger_message(user_client, chat_id, prompt, agent_name, agent_id)
        await wait_for_reply()

    # The agent has no subject UUID for the user, so it stores the preference as an
    # organization-scoped memory; the known subjects are passed as a fallback.
    memories = await _wait_for_memories_containing(
        agent_client,
        marker,
        [user_id, agent_id],
        timeout=e2e_config.e2e_timeout,
    )
    assert memories, f"Expected LangGraph to store a memory containing {marker}"
