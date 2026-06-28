"""E2E test for Agno memory tool usage at organization and subject scope.

A generic "secretary" agent is given Band memory tools (``Capability.MEMORY``)
but its developer instructions never mention scope/system/type/segment. Correct
behavior therefore depends on the injected ``MEMORY_SECTION`` guidance, so a
passing test validates both the memory tools and the prompt-injection feature
(the Agno adapter appends ``MEMORY_SECTION`` to the agent's system prompt when
the memory capability is enabled).

Memory plumbing (unique markers, polling, teardown cleanup) comes from the shared
``memory`` fixture (``MemoryProbe``); the trigger-and-wait flow from
``send_and_wait_for_reply``. New memory tests should reuse those rather than
re-implementing them.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/adapters/test_agno_memory.py -v -s --no-cov
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from band_rest import AsyncRestClient

from band import Agent
from band.core.types import AdapterFeatures, Capability, Emit
from tests.e2e.settings import (
    E2ESettings,
    RoomAllocator,
    requires_e2e,
    requires_openai,
)
from tests.e2e.helpers import (
    MemoryProbe,
    TrackingWebSocketClient,
    running_agent,
    send_and_wait_for_reply,
)

# Deliberately generic — no mention of scope/system/type/segment, so the agent
# must rely on the injected MEMORY_SECTION guidance to store memories correctly.
SECRETARY_INSTRUCTIONS = (
    "You are a personal secretary who helps the user remember facts for the long "
    "run. Whenever the user shares something worth remembering, remember it "
    "so you can recall it in future conversations, then briefly "
    "confirm. Keep responses short."
)


@pytest.fixture
async def agno_memory_room(
    e2e_fresh_room_allocator: RoomAllocator,
) -> tuple[str, str, str]:
    # A fresh room per test: the memory agent must not inherit unrelated history
    # from reused rooms, which derails small models and pollutes the scope check.
    return await e2e_fresh_room_allocator("agno-memory")


@pytest.fixture
async def running_agno_memory_agent(
    e2e_config: E2ESettings,
) -> AsyncGenerator[Agent, None]:
    """Run an Agno secretary agent with Band memory tools enabled.

    Uses ``running_agent`` so the connect is retried with a cooldown when the
    platform rate-limits a rapid reconnect after a recent supersede (HTTP 429),
    which happens when both tests in this module run against one agent_id.
    """
    from agno.agent import Agent as AgnoAgent
    from agno.models.openai import OpenAIChat

    from band.adapters.agno import AgnoAdapter

    agno_agent = AgnoAgent(
        model=OpenAIChat(id=e2e_config.e2e_llm_model),
        instructions=SECRETARY_INSTRUCTIONS,
    )
    # Emit.EXECUTION posts the agent's tool_call/tool_result events to the room,
    # so a failing run can be debugged by inspecting what the agent actually did
    # (e.g. via band's REST context) instead of guessing.
    adapter = AgnoAdapter(
        agno_agent,
        features=AdapterFeatures(
            capabilities={Capability.MEMORY},
            emit={Emit.EXECUTION},
        ),
    )

    async with running_agent(
        adapter,
        agent_id=e2e_config.test_agent_id,
        api_key=e2e_config.band_api_key,
        config=e2e_config,
    ) as agent:
        yield agent


# loop_scope="session" runs the agent's background task on the test's event loop
# so it processes the trigger concurrently with the test body.
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.flaky(reruns=2)
@requires_e2e
@requires_openai
async def test_agno_secretary_stores_organization_memory(
    e2e_config: E2ESettings,
    agno_memory_room: tuple[str, str, str],
    e2e_agent_info: tuple[str, str],
    e2e_user_client: AsyncRestClient,
    running_agno_memory_agent: Agent,
    ws_client: TrackingWebSocketClient,
    memory: MemoryProbe,
) -> None:
    """A shared/company fact is stored as an organization-scoped memory."""
    chat_id, _user_id, _user_name = agno_memory_room
    agent_id, agent_name = e2e_agent_info
    marker = memory.marker("Q3LAUNCH")
    prompt = (
        "Remember this for the whole organization (so it can be shared "
        f"everywhere): the code name for our Q3 launch is {marker}."
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


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.flaky(reruns=2)
@requires_e2e
@requires_openai
async def test_agno_secretary_stores_subject_memory(
    e2e_config: E2ESettings,
    agno_memory_room: tuple[str, str, str],
    e2e_agent_info: tuple[str, str],
    e2e_user_client: AsyncRestClient,
    running_agno_memory_agent: Agent,
    ws_client: TrackingWebSocketClient,
    memory: MemoryProbe,
) -> None:
    """A personal fact is stored as a subject-scoped memory linked to the user.

    The agent is only told the fact is "about me specifically" — it must infer
    subject scope and resolve the user's subject_id (via band_get_participants /
    band_lookup_peers) from the injected memory-scope guidance.
    """
    chat_id, user_id, _user_name = agno_memory_room
    agent_id, agent_name = e2e_agent_info
    marker = memory.marker("BADGE")
    prompt = (
        "Remember this about me personally so you recall it whenever we talk: "
        f"my employee badge number is {marker}. Save it as being about me "
        "specifically."
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

    matches = await memory.wait(marker, scope="subject", subject_id=user_id)
    assert any(getattr(m, "subject_id", None) == user_id for m in matches), (
        f"Expected a subject memory containing {marker} linked to subject "
        f"{user_id}, but matched subjects were "
        f"{[getattr(m, 'subject_id', None) for m in matches]}."
    )
