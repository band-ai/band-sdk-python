"""E2E test for Agno memory tool usage at organization and subject scope.

A generic "secretary" agent is given Band memory tools (``Capability.MEMORY``)
but its developer instructions never mention scope/system/type/segment. Correct
behavior therefore depends on the injected ``MEMORY_SECTION`` guidance, so a
passing test validates both the memory tools and the prompt-injection feature
(the Agno adapter appends ``MEMORY_SECTION`` to the agent's system prompt when
the memory capability is enabled).

Each remembered fact carries a per-run UUID marker so it is identifiable on the
live platform; the created memories are archived in teardown unless ``--no-clean``
(or ``BAND_TEST_NO_CLEAN``) is set.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/adapters/test_agno_memory.py -v -s --no-cov
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import pytest
from band_rest import AsyncRestClient

from band import Agent
from band.core.types import AdapterFeatures, Capability
from tests.conftest_integration import is_no_clean_mode
from tests.e2e.conftest import (
    E2ESettings,
    RoomAllocator,
    requires_e2e,
    requires_openai,
)
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    listening_for_agent_responses,
    running_agent,
    send_trigger_message,
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
    e2e_room_allocator: RoomAllocator,
) -> tuple[str, str, str]:
    return await e2e_room_allocator("agno-memory")


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
    adapter = AgnoAdapter(
        agno_agent,
        features=AdapterFeatures(capabilities={Capability.MEMORY}),
    )

    async with running_agent(
        adapter,
        agent_id=e2e_config.test_agent_id,
        api_key=e2e_config.band_api_key,
        config=e2e_config,
    ) as agent:
        yield agent


@pytest.fixture
async def archived_memory_ids(
    e2e_session_client: AsyncRestClient,
    request: pytest.FixtureRequest,
) -> AsyncGenerator[list[str], None]:
    """Collect memory IDs created by a test and archive them on teardown.

    Tests append the IDs they verified. Archiving (hide but preserve) keeps the
    live organization clean across runs. Honors ``--no-clean`` /
    ``BAND_TEST_NO_CLEAN`` so data can be inspected after a run.
    """
    ids: list[str] = []
    yield ids

    if is_no_clean_mode(request):
        return
    for memory_id in ids:
        with contextlib.suppress(Exception):
            await e2e_session_client.agent_api_memories.archive_agent_memory(
                id=memory_id
            )


async def _wait_for_memories(
    client: AsyncRestClient,
    marker: str,
    *,
    scope: str,
    timeout: float,
) -> list[Any]:
    """Poll until active ``scope`` memories contain ``marker``; return the matches."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        response = await client.agent_api_memories.list_agent_memories(
            page_size=50, status="active", scope=scope
        )
        matches = [
            memory
            for memory in response.data or []
            if marker in (getattr(memory, "content", None) or "")
        ]
        if matches:
            return matches
        await asyncio.sleep(1)

    pytest.fail(f"Expected {scope} memory containing {marker}")


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
    e2e_session_client: AsyncRestClient,
    e2e_user_client: AsyncRestClient,
    running_agno_memory_agent: Agent,
    ws_client: TrackingWebSocketClient,
    archived_memory_ids: list[str],
) -> None:
    """A shared/company fact is stored as an organization-scoped memory."""
    chat_id, _user_id, _user_name = agno_memory_room
    agent_id, agent_name = e2e_agent_info
    marker = f"AGNO_MEM_ORG_{uuid4().hex}"
    prompt = (
        f"Remember this for the whole organization (so it can be shared everywhere): {marker} is the code name for our "
        "Q3 launch."
    )

    async with listening_for_agent_responses(
        ws_client, chat_id, timeout=e2e_config.e2e_timeout, raise_on_timeout=True
    ) as wait_for_reply:
        await send_trigger_message(
            e2e_user_client, chat_id, prompt, agent_name, agent_id
        )
        await wait_for_reply()

    matches = await _wait_for_memories(
        e2e_session_client,
        marker,
        scope="organization",
        timeout=e2e_config.e2e_timeout,
    )
    archived_memory_ids.extend(m.id for m in matches)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.flaky(reruns=2)
@requires_e2e
@requires_openai
async def test_agno_secretary_stores_subject_memory(
    e2e_config: E2ESettings,
    agno_memory_room: tuple[str, str, str],
    e2e_agent_info: tuple[str, str],
    e2e_session_client: AsyncRestClient,
    e2e_user_client: AsyncRestClient,
    running_agno_memory_agent: Agent,
    ws_client: TrackingWebSocketClient,
    archived_memory_ids: list[str],
) -> None:
    """A personal fact is stored as a subject-scoped memory linked to the user.

    The agent is only told the fact is "about me specifically" — it must infer
    subject scope and resolve the user's subject_id (via band_lookup_peers / the
    participant list) from the injected memory-scope guidance.
    """
    chat_id, user_id, _user_name = agno_memory_room
    agent_id, agent_name = e2e_agent_info
    marker = f"AGNO_MEM_SUBJ_{uuid4().hex}"
    prompt = (
        "Remember this about me personally so you recall it whenever we talk: "
        f"{marker} — I prefer espresso over drip coffee. Save it as being about "
        "me specifically."
    )

    async with listening_for_agent_responses(
        ws_client, chat_id, timeout=e2e_config.e2e_timeout, raise_on_timeout=True
    ) as wait_for_reply:
        await send_trigger_message(
            e2e_user_client, chat_id, prompt, agent_name, agent_id
        )
        await wait_for_reply()

    matches = await _wait_for_memories(
        e2e_session_client,
        marker,
        scope="subject",
        timeout=e2e_config.e2e_timeout,
    )
    assert any(getattr(m, "subject_id", None) == user_id for m in matches), (
        f"Expected a subject memory containing {marker} linked to subject "
        f"{user_id}, but matched subjects were "
        f"{[getattr(m, 'subject_id', None) for m in matches]}."
    )
    archived_memory_ids.extend(m.id for m in matches)
