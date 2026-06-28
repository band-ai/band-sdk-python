"""Adapter factory fixtures for E2E tests.

Provides factory functions that create real adapter instances configured
with cheap LLM models for E2E testing. Each factory returns an adapter
ready to be used with Agent.create().

Run with:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/adapters/ -v -s --no-cov
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

import pytest

from band.core.simple_adapter import SimpleAdapter

from tests.e2e.settings import E2ESettings

logger = logging.getLogger(__name__)

# Type alias for adapter factory functions
AdapterFactory = Callable[[E2ESettings], SimpleAdapter[Any]]


# =============================================================================
# Individual Adapter Factories
# =============================================================================


def _require_openai_key() -> None:
    """Skip test if OPENAI_API_KEY is not set."""
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")


def _require_anthropic_key() -> None:
    """Skip test if ANTHROPIC_API_KEY is not set."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")


def create_langgraph_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a LangGraph adapter with a cheap OpenAI model."""
    _require_openai_key()
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import MemorySaver

    from band.adapters.langgraph import LangGraphAdapter

    return LangGraphAdapter(
        llm=ChatOpenAI(model=settings.e2e_llm_model),
        checkpointer=MemorySaver(),
        custom_section="Keep responses short and concise. Always respond using the band_send_message tool.",
    )


def create_anthropic_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create an Anthropic adapter with a cheap Claude model."""
    _require_anthropic_key()
    from band.adapters.anthropic import AnthropicAdapter

    return AnthropicAdapter(
        model=settings.e2e_anthropic_model,
        custom_section="Keep responses short and concise.",
    )


def create_pydantic_ai_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a Pydantic AI adapter with a cheap OpenAI model."""
    _require_openai_key()
    from band.adapters.pydantic_ai import PydanticAIAdapter

    return PydanticAIAdapter(
        model=f"openai:{settings.e2e_llm_model}",
        custom_section="Keep responses short and concise.",
    )


def create_claude_sdk_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a Claude SDK adapter with a cheap Claude model."""
    _require_anthropic_key()
    from band.adapters.claude_sdk import ClaudeSDKAdapter

    return ClaudeSDKAdapter(
        model=settings.e2e_anthropic_model,
        custom_section="Keep responses short and concise.",
    )


def create_crewai_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a CrewAI adapter with a cheap OpenAI model."""
    _require_openai_key()
    from band.adapters.crewai import CrewAIAdapter

    return CrewAIAdapter(
        model=settings.e2e_llm_model,
        role="Test Assistant",
        goal="Help users with simple tasks for testing",
        backstory="A test agent for E2E validation.",
        custom_section="Keep responses short and concise.",
    )


def create_agno_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create an Agno adapter for the cross-adapter E2E suite.

    Use a strong instruction-following model via ``E2E_ANTHROPIC_MODEL``
    (e.g. ``claude-sonnet-4-6``). Cheap/small models (e.g. Haiku) refuse the
    suite's crafted trigger prompts as prompt-injection; Sonnet 4.6 clears
    ``test_tool_execution_send_message[agno]`` (echo a code word) and
    ``test_agents_in_different_rooms_isolated[agno]``.

    Note: the room-isolation trigger prompts are framed as a neutral "note"
    rather than a "secret code". A "secret code → recall it" prompt reads as a
    credential/embedded directive and gets refused even by Sonnet 4.6, which is
    unrelated to isolation; the neutral wording avoids that false failure. See
    ``tests/e2e/scenarios/test_room_isolation.py``.
    """
    _require_anthropic_key()
    from agno.agent import Agent as AgnoAgent
    from agno.models.anthropic import Claude

    from band.adapters.agno import AgnoAdapter

    agno_agent = AgnoAgent(
        model=Claude(id=settings.e2e_anthropic_model),
        instructions="Keep responses short and concise.",
    )
    return AgnoAdapter(agno_agent)


# =============================================================================
# Adapter Registry
# =============================================================================

ADAPTER_FACTORIES: dict[str, AdapterFactory] = {
    "langgraph": create_langgraph_adapter,
    "anthropic": create_anthropic_adapter,
    "pydantic_ai": create_pydantic_ai_adapter,
    "claude_sdk": create_claude_sdk_adapter,
    "crewai": create_crewai_adapter,
    "agno": create_agno_adapter,
}

# Note: Parlant is excluded from the default parametrized set because it
# requires a running Parlant server and more complex setup (server + agent).
# It has its own dedicated test file.


# Note: The parametrized `adapter_entry` fixture lives in tests/e2e/conftest.py
# so it is shared between adapters/ and scenarios/ tests.
