"""E2E test for LangGraph memory tool usage.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/adapters/test_langgraph_memory.py -v -s --no-cov
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from uuid import uuid4

import pytest
from thenvoi_rest import AsyncRestClient

from band import Agent
from band.adapters.langgraph import LangGraphAdapter
from band.core.types import AdapterFeatures, Capability
from tests.e2e.conftest import E2ESettings, requires_e2e, requires_openai
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    listening_for_agent_responses,
    send_trigger_message,
)

RoomAllocator = Callable[[str], Awaitable[tuple[str, str, str]]]

MEMORY_CUSTOM_SECTION = (
    "When asked to remember durable information, call `band_store_memory` "
    'before replying. If you do not have a real subject_id, use scope="organization" '
    "and omit subject_id."
)


@pytest.fixture
async def langgraph_memory_room(
    e2e_room_allocator: RoomAllocator,
) -> tuple[str, str, str]:
    return await e2e_room_allocator("langgraph-memory")


@pytest.fixture
async def running_langgraph_memory_agent(
    e2e_config: E2ESettings,
) -> AsyncGenerator[Agent, None]:
    """Run a LangGraph agent configured with Band memory tools enabled."""
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


async def _wait_for_org_memory_containing(
    client: AsyncRestClient,
    marker: str,
    *,
    timeout: float,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout

    while asyncio.get_running_loop().time() < deadline:
        response = await client.agent_api_memories.list_agent_memories(
            page_size=50,
            status="active",
            scope="organization",
        )
        if any(
            marker in (getattr(memory, "content", None) or "")
            for memory in response.data or []
        ):
            return

        await asyncio.sleep(1)

    pytest.fail(f"Expected organization memory containing {marker}")


# loop_scope="session" pins the test to the same event loop as the agent's
# background processing task, so the agent processes the trigger concurrently with
# the test body. A bare @pytest.mark.asyncio would run the body on a separate loop
# and the agent couldn't process the message until fixture teardown.
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.flaky(reruns=2)
@requires_e2e
@requires_openai
async def test_langgraph_agent_stores_durable_user_memory(
    e2e_config: E2ESettings,
    langgraph_memory_room: tuple[str, str, str],
    e2e_agent_info: tuple[str, str],
    e2e_session_client: AsyncRestClient,
    e2e_user_client: AsyncRestClient,
    running_langgraph_memory_agent: Agent,
    ws_client: TrackingWebSocketClient,
) -> None:
    """Ask LangGraph to remember a durable preference and verify it is stored."""
    chat_id, _user_id, _user_name = langgraph_memory_room
    agent_id, agent_name = e2e_agent_info
    marker = f"LANGGRAPH_MEMORY_E2E_{uuid4().hex}"
    prompt = (
        "Remember this durable preference exactly: "
        f"{marker} means I prefer concise memory test responses. "
        "Store it as a long-term semantic user memory, then acknowledge it briefly."
    )

    async with listening_for_agent_responses(
        ws_client, chat_id, timeout=e2e_config.e2e_timeout, raise_on_timeout=True
    ) as wait_for_reply:
        await send_trigger_message(
            e2e_user_client,
            chat_id,
            prompt,
            agent_name,
            agent_id,
        )
        await wait_for_reply()

    await _wait_for_org_memory_containing(
        e2e_session_client,
        marker,
        timeout=e2e_config.e2e_timeout,
    )
