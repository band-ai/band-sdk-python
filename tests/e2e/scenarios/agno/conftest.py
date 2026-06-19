"""Shared fixtures and helpers for Agno E2E scenarios.

Agno-specific building blocks live here so the scenario test modules stay
focused on the flow being verified:

- adapter builders (``create_calculator_agno_adapter``, ``build_assistant_adapter``,
  ``build_thinking_adapter``)
- the grocery-list fixture data used by the multi-agent scenarios
- direct-REST assertion helpers (tool execution, reported total, participant
  presence)
- dedicated room fixtures

Generic, framework-agnostic E2E utilities (WebSocket listeners, trigger
messages, pretty logging, the second-agent fixtures, and the ``running_agent``
lifecycle context manager) remain in ``tests/e2e/helpers.py`` and
``tests/e2e/conftest.py``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import pytest
from band_rest import AsyncRestClient

from band.core.simple_adapter import SimpleAdapter

from tests.conftest_integration import fetch_all_context
from tests.e2e.adapters.conftest import _require_anthropic_key
from tests.e2e.settings import E2ESettings, RoomAllocator
from tests.e2e.helpers import find_tool_call_in_context, log_step

if TYPE_CHECKING:
    from band.adapters.agno import AgnoAdapter

logger = logging.getLogger(__name__)

CALCULATOR_TOOL = "add_numbers"

# Grocery prices chosen to sum cleanly in float (no rounding surprises) to a
# distinctive total. Keep representations the LLM is likely to echo.
GROCERY_ITEMS: list[tuple[str, float]] = [
    ("Milk", 3.50),
    ("Bread", 2.50),
    ("Eggs", 5.00),
    ("Coffee", 12.00),
    ("Cheese", 7.50),
]
GROCERY_TOTAL = sum(price for _, price in GROCERY_ITEMS)  # 30.50
# Accept both "30.5" and "30.50" formatting from the model.
TOTAL_STRINGS = ("30.50", "30.5")


def grocery_list_text() -> str:
    """Render the grocery list with prices as a single user-facing line."""
    return ", ".join(f"{name} ${price:.2f}" for name, price in GROCERY_ITEMS)


# =============================================================================
# Adapter builders
# =============================================================================


def add_numbers(numbers: list[float]) -> float:
    """Add a list of numbers and return the total.

    Native Agno tool used by the calculator agent in the multi-agent scenarios.
    """
    total = sum(numbers)
    logger.info("Calculator tool add_numbers(%s) -> %s", numbers, total)
    return total


def create_calculator_agno_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create an Agno "calculator" adapter that reports tool executions.

    The agent owns a native ``add_numbers`` tool; ``Emit.EXECUTION`` makes the
    adapter post ``tool_call``/``tool_result`` events to the room so a test can
    verify (via direct REST query) that the tool actually ran.
    """
    _require_anthropic_key()
    from agno.agent import Agent as AgnoAgent
    from agno.models.anthropic import Claude

    from band.adapters.agno import AgnoAdapter
    from band.core.types import AdapterFeatures, Emit

    agno_agent = AgnoAgent(
        model=Claude(id=settings.e2e_anthropic_model),
        instructions=(
            "You are a calculator agent. When asked to add up numbers, you MUST "
            "use the add_numbers tool to compute the total -- never do the "
            "arithmetic yourself. Reply with the total using the band_send_message "
            "tool. Keep responses short."
        ),
        tools=[add_numbers],
    )
    return AgnoAdapter(
        agno_agent,
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )


def build_assistant_adapter(
    settings: E2ESettings,
    *,
    calculator_id: str,
    calculator_name: str,
) -> SimpleAdapter[Any]:
    """Build the "helpful assistant" Agno adapter (Agent A).

    The assistant has no tools of its own but receives Band's chat/participant
    tools by default. Its instructions direct it to bring in the calculator
    agent, ask it for the total, relay the answer, and remove it.
    """
    from agno.agent import Agent as AgnoAgent
    from agno.models.anthropic import Claude

    from band.adapters.agno import AgnoAdapter

    instructions = (
        "You are a helpful shopping assistant chatting with a user about their "
        "grocery list. You are TERRIBLE at arithmetic and must NEVER add numbers "
        "yourself. There is a calculator agent you can bring into the room:\n"
        f"  - name: {calculator_name}\n"
        f"  - id: {calculator_id}\n"
        "When the user asks for the total cost, do ALL of the following, in order:\n"
        f"  1. Call band_add_participant with identifier '{calculator_id}' to add "
        "the calculator agent to this room.\n"
        "  2. Call band_send_message with a message that @mentions the calculator "
        f"(mention id {calculator_id}, name {calculator_name}), listing every item "
        "and its price and asking it to add the prices up.\n"
        "  3. When the calculator replies with the total, call band_send_message to "
        "tell the user the total (mention the user).\n"
        f"  4. Finally, call band_remove_participant with identifier "
        f"'{calculator_id}' to remove the calculator agent from the room.\n"
        "Keep every message short."
    )
    agno_agent = AgnoAgent(
        model=Claude(id=settings.e2e_anthropic_model),
        instructions=instructions,
    )
    return AgnoAdapter(agno_agent)


def build_db_backed_agno_adapter(
    settings: E2ESettings,
    *,
    db: Any,
    session_id: str,
) -> "AgnoAdapter":
    """Build an Agno adapter whose agent owns its history via a database.

    ``add_history_to_context=True`` with a ``db`` makes Agno persist and replay
    prior turns itself (keyed by ``session_id``). The AgnoAdapter detects this
    and disables Band's own history rehydration, so prior context comes from
    Agno alone and is not duplicated. Passing the *same* ``db`` object and
    ``session_id`` to a second adapter instance models a restart against a
    persistent backend.
    """
    _require_anthropic_key()
    from agno.agent import Agent as AgnoAgent
    from agno.models.anthropic import Claude

    from band.adapters.agno import AgnoAdapter

    agno_agent = AgnoAgent(
        model=Claude(id=settings.e2e_anthropic_model),
        db=db,
        session_id=session_id,
        add_history_to_context=True,
        instructions=(
            "You are a helpful assistant with a long-term memory. Whenever you "
            "acknowledge OR recall a value you were asked to remember, you MUST "
            "include the exact value verbatim in your reply. Keep responses short."
        ),
    )
    return AgnoAdapter(agno_agent)


def build_thinking_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Build an Agno adapter with reasoning enabled and thought reporting on.

    The Claude model is created with native extended thinking enabled
    (``thinking=...``). That makes Agno treat it as a native reasoning model and
    populate ``reasoning_content`` from Claude's own thinking output; without it,
    Agno falls back to a structured-output chain-of-thought agent that returns
    empty content for Claude, leaving ``reasoning_content`` blank and no thought
    to emit. ``reasoning=True`` selects the native-reasoning dispatch and
    ``Emit.THOUGHTS`` makes the adapter post that reasoning as a ``thought``
    event to the room.
    """
    _require_anthropic_key()
    from agno.agent import Agent as AgnoAgent
    from agno.models.anthropic import Claude

    from band.adapters.agno import AgnoAdapter
    from band.core.types import AdapterFeatures, Emit

    agno_agent = AgnoAgent(
        model=Claude(
            id=settings.e2e_anthropic_model,
            thinking={"type": "enabled", "budget_tokens": 1024},
        ),
        instructions=(
            "You are a careful assistant. Think through problems step by step "
            "before answering. Keep your final answer short."
        ),
        reasoning=True,
    )
    return AgnoAdapter(
        agno_agent,
        features=AdapterFeatures(emit={Emit.THOUGHTS}),
    )


# =============================================================================
# Lifecycle + assertion helpers
# =============================================================================


async def wait_participant_absent(
    client: AsyncRestClient,
    room_id: str,
    participant_id: str,
    *,
    timeout: float = 30.0,
    poll_interval: float = 3.0,
) -> bool:
    """Poll the participant list until *participant_id* is gone or timeout."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        resp = await client.agent_api_participants.list_agent_chat_participants(room_id)
        ids = [p.id for p in (resp.data or [])]
        if participant_id not in ids:
            return True
        await asyncio.sleep(poll_interval)
    return False


async def participant_present(
    client: AsyncRestClient,
    room_id: str,
    participant_id: str,
) -> bool:
    """Return True if *participant_id* is currently a room participant."""
    resp = await client.agent_api_participants.list_agent_chat_participants(room_id)
    return participant_id in [p.id for p in (resp.data or [])]


async def assert_calculator_ran(
    calculator_client: AsyncRestClient,
    room_id: str,
) -> None:
    """Assert (via direct REST query) the calculator's tool actually executed.

    Queries with the calculator's own client so its emitted ``tool_call``
    events are visible, then checks for an ``add_numbers`` execution.
    """
    items = await fetch_all_context(calculator_client, room_id)
    used = find_tool_call_in_context(items, CALCULATOR_TOOL)
    assert used, (
        f"Expected a '{CALCULATOR_TOOL}' tool_call event in room {room_id}, "
        f"but found none in {len(items)} context item(s). The calculator agent "
        "did not run its tool."
    )
    log_step("assert", f"calculator tool '{CALCULATOR_TOOL}' executed")


async def assert_thought_emitted(
    client: AsyncRestClient,
    room_id: str,
) -> list[Any]:
    """Assert (via direct REST query) at least one ``thought`` event exists.

    Agent-emitted events (``thought``, ``tool_call``, ``tool_result``) are
    surfaced by the ``agent_api_context`` endpoint but are NOT delivered over
    the user's WebSocket ``message_created`` stream (which carries only
    ``text``). Always assert events via REST, not the socket.
    """
    items = await fetch_all_context(client, room_id)
    thoughts = [
        item for item in items if getattr(item, "message_type", None) == "thought"
    ]
    assert thoughts, (
        f"Expected a 'thought' event in room {room_id} context, but found none "
        f"among {len(items)} item(s). The reasoning agent did not emit a thought."
    )
    log_step("assert", f"{len(thoughts)} thought event(s) present via REST")
    return thoughts


async def assert_total_reported(
    user_client: AsyncRestClient,
    room_id: str,
) -> None:
    """Assert (via direct REST query) the total appears in a room message."""
    items = await fetch_all_context(user_client, room_id)
    texts = [
        getattr(item, "content", "") or ""
        for item in items
        if getattr(item, "message_type", None) == "text"
    ]
    found = any(any(t in text for t in TOTAL_STRINGS) for text in texts)
    assert found, (
        f"Expected the total ({GROCERY_TOTAL:.2f}) to appear in a room message, "
        f"but it was not found among {len(texts)} text message(s)."
    )
    log_step("assert", f"total {GROCERY_TOTAL:.2f} reported in room")


# =============================================================================
# Room fixtures
# =============================================================================


@pytest.fixture
async def agno_multi_room(
    e2e_room_allocator: RoomAllocator,
) -> tuple[str, str, str]:
    """Dedicated room for the multi-agent Agno scenarios.

    Returns (room_id, user_id, user_name). The room starts with Agent A (its
    creator) and the User; Agent B is added during the flow.
    """
    return await e2e_room_allocator("agno_multi_agent")


@pytest.fixture
async def agno_thoughts_room(
    e2e_room_allocator: RoomAllocator,
) -> tuple[str, str, str]:
    """Dedicated room for the Agno thoughts scenario."""
    return await e2e_room_allocator("agno_thoughts")


@pytest.fixture
async def agno_database_room(
    e2e_fresh_room_allocator: RoomAllocator,
) -> tuple[str, str, str]:
    """Fresh, uncontaminated room for the db-backed Agno restart scenario.

    Uses the fresh-room allocator (not the reusing one) on purpose: this
    scenario disables Band's history rehydration, so the agent's only memory is
    Agno's ephemeral db. A reused room's stale "remember X" messages from prior
    runs would otherwise be answered on bootstrap and contaminate the recall
    assertion with an old secret.
    """
    return await e2e_fresh_room_allocator("agno_database_restart")
