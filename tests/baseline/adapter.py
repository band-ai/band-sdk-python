"""Shared adapter identity and discovery for baseline conformance suites."""

from __future__ import annotations

import pkgutil
from enum import StrEnum

import band.adapters


class Adapter(StrEnum):
    """Typed handle for a baseline LLM-agent adapter.

    Values match the adapter module names under ``band.adapters``.  Both the
    offline and E2E baseline suites import this type, keeping adapter identity
    single-sourced while their construction and execution paths remain separate.
    """

    ANTHROPIC = "anthropic"
    CLAUDE_SDK = "claude_sdk"
    COPILOT_SDK = "copilot_sdk"
    COPILOT_ACP = "copilot_acp"
    LANGGRAPH = "langgraph"
    PYDANTIC_AI = "pydantic_ai"
    GEMINI = "gemini"
    GOOGLE_ADK = "google_adk"
    CREWAI = "crewai"
    AGNO = "agno"
    CREWAI_FLOW = "crewai_flow"
    CODEX = "codex"
    OPENCODE = "opencode"
    LETTA = "letta"


# These modules bridge Band to another protocol or require a bespoke external
# service lifecycle, so they are deliberately outside the shared agent matrix.
NON_AGENT_ADAPTERS = frozenset({"a2a", "a2a_gateway", "acp", "slack", "parlant"})


def discovered_agent_ids() -> set[str]:
    """Return agent-adapter module ids without importing optional dependencies."""
    names = {
        module.name
        for module in pkgutil.iter_modules(band.adapters.__path__)
        if not module.name.startswith("_")
    }
    return names - NON_AGENT_ADAPTERS


def assert_adapter_ids_cover_discovered() -> None:
    """Fail when adapter identity drifts from the source tree."""
    enum_values = {adapter.value for adapter in Adapter}
    discovered = discovered_agent_ids()
    assert enum_values == discovered, (
        "Baseline Adapter enum is out of sync with src/band/adapters: "
        f"missing={sorted(discovered - enum_values)} "
        f"extra={sorted(enum_values - discovered)}"
    )
