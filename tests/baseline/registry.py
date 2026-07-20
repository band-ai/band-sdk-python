"""Baseline adapter support registry.

Every adapter in the shared baseline matrix has an entry. A supported entry names
a deterministic model-output injection path; an unsupported entry records why it
is not yet covered by this offline harness rather than silently disappearing from
scope.
"""

from __future__ import annotations

from dataclasses import dataclass

from tests.baseline.adapter import Adapter, assert_adapter_ids_cover_discovered


@dataclass(frozen=True)
class AdapterSupport:
    """Whether an adapter has a shared offline injection path."""

    adapter: Adapter
    injection: str | None = None
    reason: str | None = None

    @property
    def supported(self) -> bool:
        return self.injection is not None


SUPPORT: tuple[AdapterSupport, ...] = (
    AdapterSupport(Adapter.ANTHROPIC, injection="AnthropicAdapter._call_anthropic"),
    AdapterSupport(
        Adapter.CLAUDE_SDK, reason="requires a Claude SDK session injection seam"
    ),
    AdapterSupport(
        Adapter.COPILOT_SDK, reason="requires a Copilot session injection seam"
    ),
    AdapterSupport(
        Adapter.COPILOT_ACP,
        reason="external Copilot ACP process is outside isolated adapter execution",
    ),
    AdapterSupport(
        Adapter.LANGGRAPH, reason="requires a graph-model decision translator"
    ),
    AdapterSupport(
        Adapter.PYDANTIC_AI, reason="requires a pydantic-ai model decision translator"
    ),
    AdapterSupport(Adapter.GEMINI, reason="requires a Gemini response translator"),
    AdapterSupport(
        Adapter.GOOGLE_ADK, reason="requires a Google ADK response translator"
    ),
    AdapterSupport(Adapter.CREWAI, reason="requires a CrewAI kickoff injection seam"),
    AdapterSupport(Adapter.AGNO, reason="requires an Agno model response translator"),
    AdapterSupport(
        Adapter.CREWAI_FLOW, reason="terminal flow has no model-output injection seam"
    ),
    AdapterSupport(
        Adapter.CODEX,
        reason="external Codex process is outside isolated adapter execution",
    ),
    AdapterSupport(
        Adapter.OPENCODE,
        reason="external OpenCode process is outside isolated adapter execution",
    ),
    AdapterSupport(
        Adapter.LETTA, reason="requires a self-hosted Letta server and MCP registration"
    ),
)


def support_for(adapter: Adapter) -> AdapterSupport:
    """Return the declared baseline support for one adapter."""
    return next(item for item in SUPPORT if item.adapter == adapter)


def assert_support_is_complete() -> None:
    """Prevent a new adapter from silently bypassing baseline consideration."""
    registered = {item.adapter for item in SUPPORT}
    assert_adapter_ids_cover_discovered()
    assert registered == set(Adapter), (
        "Baseline support registry is incomplete: "
        f"missing={sorted(set(Adapter) - registered)} extra={sorted(registered - set(Adapter))}"
    )
    for item in SUPPORT:
        assert item.injection or item.reason, (
            f"{item.adapter} needs an injection seam or reason"
        )
