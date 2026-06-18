"""E2E test for LangGraph memory tool usage.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/adapters/test_langgraph_memory.py -v -s --no-cov
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable

import pytest
from band_rest import AsyncRestClient

from band import Agent
from band.adapters.langgraph import LangGraphAdapter
from band.core.types import AdapterFeatures, Capability
from tests.e2e.conftest import E2ESettings, requires_e2e, requires_openai
from tests.e2e.helpers import (
    MemoryProbe,
    TrackingWebSocketClient,
    send_and_wait_for_reply,
)

RoomAllocator = Callable[[str], Awaitable[tuple[str, str, str]]]

MEMORY_CUSTOM_SECTION = (
    "When asked to remember durable information, call `band_store_memory` "
    "before replying."
)


@pytest.fixture
async def langgraph_memory_room(
    e2e_fresh_room_allocator: RoomAllocator,
) -> tuple[str, str, str]:
    # A fresh room per test: the memory agent must not inherit unrelated history
    # from reused rooms, which derails the model and can stall its reply.
    return await e2e_fresh_room_allocator("langgraph-memory")


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
    e2e_user_client: AsyncRestClient,
    running_langgraph_memory_agent: Agent,
    ws_client: TrackingWebSocketClient,
    memory: MemoryProbe,
) -> None:
    """Ask LangGraph to remember a durable preference and verify it is stored."""
    chat_id, _user_id, _user_name = langgraph_memory_room
    agent_id, agent_name = e2e_agent_info
    marker = memory.marker("LGMEM")
    prompt = (
        "Remember this for the whole organization so anyone can recall it: the "
        f"project code phrase {marker} means we keep responses concise. "
        "Acknowledge it briefly."
    )

    await send_and_wait_for_reply(
        ws_client,
        e2e_user_client,
        chat_id,
        prompt,
        agent_name,
        agent_id,
        timeout=e2e_config.e2e_timeout,
    )

    await memory.wait(marker, scope="organization")
