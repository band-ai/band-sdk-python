"""Adapter discovery + generic construction for the baseline matrix (pytest-free).

This is the one place that knows which framework adapters exist and how to build a
ready-to-run instance of each. Tests never hard-code an adapter list: they iterate
the registry, so L0-L4 scenarios and the smokes are written once and run across the
whole matrix.

Adding a framework
------------------
Two edits, both here: add an ``Adapter`` enum member (value == the module name)
and a decorated builder::

    class Adapter(StrEnum):
        ...
        MYFRAMEWORK = "myframework"

    @adapter(Adapter.MYFRAMEWORK, requires=[Dep.OPENAI], supports=[Capability.MEMORY])
    def _build_myframework(settings, *, prompt, features):
        from band.adapters.myframework import MyframeworkAdapter
        return MyframeworkAdapter(
            model=settings.llm_models.openai_model,
            custom_section=prompt,
            features=features,
        )

The discovery guard (``assert_registry_covers_discovered``) scans
``src/band/adapters/`` and **fails loudly** unless the enum, the registry, and the
discovered (non-bridge) modules all agree -- so a newly-added adapter cannot be
silently skipped, and it names exactly which of the two edits is missing.

Construction is parametrizable: every builder takes ``prompt`` (a steering system
prompt, mapped to whichever constructor argument the framework uses) and
``features`` (``AdapterFeatures`` -- this is how a test enables memory/contacts/
execution emission). ``supports`` declares the capabilities a test can select on
(e.g. "all adapters supporting memory"); it does not itself enable them -- pass the
matching ``features`` to actually turn a capability on.

Gating policy: each entry declares its requirements as ``Dep`` members; an absent
requirement **fails** the cell with the env-var/CLI/server reason (never skips).
Heavy/optional framework imports live **inside** each builder so importing this
module (which triggers registration) never pulls in an absent dependency.
"""

from __future__ import annotations

import pkgutil
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import band.adapters

from band.core.simple_adapter import SimpleAdapter
from band.core.types import AdapterFeatures, Capability
from band.runtime.custom_tools import CustomToolDef

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.requirements import Dep


class Adapter(StrEnum):
    """Typed handle for a registered LLM-agent adapter.

    Each member's *value* equals its module name under ``band.adapters`` so a test
    references an adapter as ``Adapter.ANTHROPIC`` (no magic string) while the
    registry, ``build_adapter`` and the discovery guard all key off the same value.
    The guard enforces three-way parity: ``Adapter`` ⇔ registry ⇔ discovered modules.
    """

    ANTHROPIC = "anthropic"
    CLAUDE_SDK = "claude_sdk"
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


# A builder turns settings (+ optional steering prompt / features) into a
# ready-to-run adapter. Heterogeneous constructors are hidden behind this seam.
AdapterBuilder = Callable[..., SimpleAdapter[Any]]


@dataclass(frozen=True)
class AdapterSpec:
    """A registered adapter: its id, requirements, capabilities, and builder."""

    id: Adapter
    requires: tuple[Dep, ...]
    supports: frozenset[Capability]
    build: AdapterBuilder = field(compare=False)


# Keyed by the adapter's string id (== ``Adapter`` value; ``Adapter`` is a str
# subclass, so both ``Adapter`` members and plain ids look up transparently).
_REGISTRY: dict[str, AdapterSpec] = {}


def adapter(
    name: Adapter,
    *,
    requires: Iterable[Dep] = (),
    supports: Iterable[Capability] = (),
) -> Callable[[AdapterBuilder], AdapterBuilder]:
    """Register ``name``'s builder in the matrix registry.

    The decorated function keeps its identity (it is returned unchanged) so it can
    also be called directly. Registering a duplicate is a programming error.
    """

    def register(build: AdapterBuilder) -> AdapterBuilder:
        if name in _REGISTRY:
            raise ValueError(f"adapter {name!r} is already registered")
        _REGISTRY[name] = AdapterSpec(
            id=name,
            requires=tuple(requires),
            supports=frozenset(supports),
            build=build,
        )
        return build

    return register


def spec_for(name: Adapter) -> AdapterSpec:
    """The registered spec for ``name`` (raises ``KeyError`` if unregistered)."""
    return _REGISTRY[name]


def _reject_tools(adapter: Adapter, tools: list[CustomToolDef] | None) -> None:
    """Fail loudly when custom tools are asked of an adapter that can't take them.

    Agno owns its agent's tool list (a per-run factory), Letta exposes tools via
    its MCP server, and pydantic-ai takes native callables (not band
    ``CustomToolDef`` tuples) — so none accept baseline custom tools here.
    Consistent with the toolkit's fail-loudly rule, reject rather than silently
    drop the tools a test requested (which would be a false green).
    """
    if tools:
        raise ValueError(
            f"the {adapter.value} adapter does not support custom tools "
            "(additional_tools); configure them on the framework directly"
        )


# =============================================================================
# Discovery: allow/deny over src/band/adapters/
# =============================================================================

# Non-agent adapters that the matrix deliberately excludes. Bridges expose Band to
# another protocol rather than running an LLM agent (a2a/a2a_gateway/acp/slack);
# parlant needs a running Parlant server + per-agent setup. Everything else under
# ``band.adapters`` must be registered above.
DENY: frozenset[str] = frozenset({"a2a", "a2a_gateway", "acp", "slack", "parlant"})


def discovered_agent_ids() -> set[str]:
    """The LLM-agent adapter ids present in ``src/band/adapters/`` (minus DENY).

    Scans module *names* only (``pkgutil`` does not import them), so an adapter
    whose optional dependency is absent is still discovered and never breaks
    collection.
    """
    names = {
        module.name
        for module in pkgutil.iter_modules(band.adapters.__path__)
        if not module.name.startswith("_")
    }
    return names - DENY


def registered_ids() -> set[str]:
    return set(_REGISTRY)


def assert_registry_covers_discovered() -> None:
    """Fail loudly on any drift between enum, registry, and discovered modules.

    Three sources must agree exactly: the ``Adapter`` enum, the ``@adapter``
    registry, and the agent modules under ``src/band/adapters/`` (minus DENY).
    A new framework with no enum member / no builder, or a stale entry (an enum
    member or builder with no module, or a module that should be in DENY) all
    surface here rather than being silently skipped.
    """
    enum_values = {member.value for member in Adapter}
    discovered = discovered_agent_ids()
    registered = {str(adapter_id) for adapter_id in registered_ids()}
    if not (enum_values == discovered == registered):
        raise AssertionError(
            "adapter registry is out of sync (Adapter enum / @adapter registry / "
            "src/band/adapters/ must match):\n"
            f"  discovered, missing an Adapter member: {sorted(discovered - enum_values)}\n"
            f"  discovered, missing an @adapter builder: {sorted(discovered - registered)}\n"
            f"  enum/registry with no module (stale or should be DENY): "
            f"{sorted((enum_values | registered) - discovered)}"
        )


# =============================================================================
# Query + construction
# =============================================================================


def specs(
    *,
    include: Collection[str] | None = None,
    exclude: Collection[str] | None = None,
    supports: Collection[Capability] | None = None,
    without: Collection[Capability] | None = None,
) -> list[AdapterSpec]:
    """The registered specs, optionally narrowed.

    ``include`` keeps only those ids; ``exclude`` drops those ids; ``supports``
    keeps only adapters advertising *all* the given capabilities (e.g.
    ``supports={Capability.MEMORY}``); ``without`` keeps only adapters advertising
    *none* of them (the complement, e.g. ``without={Capability.MEMORY}`` for the
    non-memory adapters). ``supports`` and ``without`` are disjoint complementary
    filters. Stable id order.
    """
    wanted = frozenset(supports or ())
    unwanted = frozenset(without or ())
    chosen = [
        spec
        for adapter_id, spec in sorted(_REGISTRY.items())
        if (include is None or adapter_id in include)
        and (exclude is None or adapter_id not in exclude)
        and wanted.issubset(spec.supports)
        and spec.supports.isdisjoint(unwanted)
    ]
    return chosen


def build_adapter(
    adapter_id: str,
    settings: BaselineSettings,
    *,
    prompt: str | None = None,
    features: AdapterFeatures | None = None,
    tools: list[CustomToolDef] | None = None,
) -> SimpleAdapter[Any]:
    """Construct the adapter registered under ``adapter_id``.

    ``prompt`` is a steering system prompt (each builder routes it to the right
    constructor argument); ``features`` flips capabilities/emission on. An unknown
    id is a programming error and names the registered set.
    """
    spec = _REGISTRY.get(adapter_id)
    if spec is None:
        raise ValueError(
            f"unknown adapter {adapter_id!r}; registered: {sorted(_REGISTRY)}"
        )
    return spec.build(settings, prompt=prompt, features=features, tools=tools)


# =============================================================================
# The matrix: one self-registering builder per LLM-agent adapter
# =============================================================================
#
# Each builder lazy-imports its framework and maps the generic ``prompt`` to the
# constructor argument that framework uses (prompt / custom_section / system_prompt
# / the agent's own instructions). ``supports`` lists the platform capabilities the
# adapter advertises for capability-scoped matrices.

_LLM_TOOL_LOOP = (Capability.MEMORY, Capability.CONTACTS)


@adapter(Adapter.ANTHROPIC, requires=[Dep.ANTHROPIC], supports=_LLM_TOOL_LOOP)
def _build_anthropic(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[CustomToolDef] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.anthropic import AnthropicAdapter

    return AnthropicAdapter(
        model=s.llm_models.anthropic_model,
        provider_key=s.llm_credentials.anthropic_api_key or None,
        prompt=prompt,
        additional_tools=tools,
        features=features,
    )


@adapter(Adapter.CLAUDE_SDK, requires=[Dep.ANTHROPIC], supports=_LLM_TOOL_LOOP)
def _build_claude_sdk(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[CustomToolDef] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.claude_sdk import ClaudeSDKAdapter

    return ClaudeSDKAdapter(
        model=s.llm_models.anthropic_model,
        custom_section=prompt,
        additional_tools=tools,
        features=features,
    )


@adapter(Adapter.LANGGRAPH, requires=[Dep.OPENAI], supports=_LLM_TOOL_LOOP)
def _build_langgraph(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[CustomToolDef] | None = None,
) -> SimpleAdapter[Any]:
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import MemorySaver

    from band.adapters.langgraph import LangGraphAdapter

    return LangGraphAdapter(
        llm=ChatOpenAI(
            model=s.llm_models.openai_model,
            api_key=s.llm_credentials.openai_api_key or None,
        ),
        checkpointer=MemorySaver(),
        custom_section=prompt or "",
        additional_tools=tools,
        features=features,
    )


@adapter(Adapter.PYDANTIC_AI, requires=[Dep.OPENAI], supports=_LLM_TOOL_LOOP)
def _build_pydantic_ai(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[CustomToolDef] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.pydantic_ai import PydanticAIAdapter

    _reject_tools(Adapter.PYDANTIC_AI, tools)
    return PydanticAIAdapter(
        model=f"openai:{s.llm_models.openai_model}",
        custom_section=prompt,
        features=features,
    )


@adapter(Adapter.GEMINI, requires=[Dep.GOOGLE], supports=_LLM_TOOL_LOOP)
def _build_gemini(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[CustomToolDef] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.gemini import GeminiAdapter

    return GeminiAdapter(
        model=s.llm_models.gemini_model,
        provider_key=s.llm_credentials.google_api_key or None,
        prompt=prompt,
        additional_tools=tools,
        features=features,
    )


@adapter(Adapter.GOOGLE_ADK, requires=[Dep.GOOGLE], supports=_LLM_TOOL_LOOP)
def _build_google_adk(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[CustomToolDef] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.google_adk import GoogleADKAdapter

    # google-adk reads the provider key / Vertex config from the environment.
    return GoogleADKAdapter(
        model=s.llm_models.gemini_model,
        custom_section=prompt,
        additional_tools=tools,
        features=features,
    )


@adapter(Adapter.CREWAI, requires=[Dep.OPENAI, Dep.CREWAI], supports=_LLM_TOOL_LOOP)
def _build_crewai(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[CustomToolDef] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.crewai import CrewAIAdapter

    return CrewAIAdapter(
        model=s.llm_models.openai_model,
        role="Test Assistant",
        goal="Help users with simple tasks for testing.",
        backstory="A test agent for E2E validation.",
        custom_section=prompt,
        additional_tools=tools,
        features=features,
    )


@adapter(Adapter.AGNO, requires=[Dep.ANTHROPIC], supports=_LLM_TOOL_LOOP)
def _build_agno(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[CustomToolDef] | None = None,
) -> SimpleAdapter[Any]:
    # Agno bridges a user-built agent, so steering goes into its instructions.
    # Use the Anthropic model: small models refuse the suite's crafted prompts as
    # injection, so the matrix relies on E2E_ANTHROPIC_MODEL being a capable model.
    from agno.agent import Agent as AgnoAgent
    from agno.models.anthropic import Claude

    from band.adapters.agno import AgnoAdapter

    _reject_tools(Adapter.AGNO, tools)
    return AgnoAdapter(
        AgnoAgent(model=Claude(id=s.llm_models.anthropic_model), instructions=prompt),
        features=features,
    )


@adapter(Adapter.CREWAI_FLOW, requires=[Dep.CREWAI])
def _build_crewai_flow(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[CustomToolDef] | None = None,
) -> SimpleAdapter[Any]:
    # CrewAI Flow returns a terminal result rather than running the Band tool loop,
    # so it takes a flow_factory (not a model/prompt) and advertises no platform
    # capabilities. The minimal flow echoes back so the reply path is observable.
    from band.adapters.crewai_flow import CrewAIFlowAdapter

    class _E2EFlow:
        async def kickoff_async(self, inputs: dict[str, Any]) -> dict[str, Any]:
            message = inputs.get("message", {})
            content = message.get("content", "") if isinstance(message, dict) else ""
            return {"decision": "direct_response", "content": content, "mentions": []}

    return CrewAIFlowAdapter(
        flow_factory=_E2EFlow, additional_tools=tools, features=features
    )


@adapter(Adapter.CODEX, requires=[Dep.CODEX_CLI, Dep.CODEX_CWD])
def _build_codex(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[CustomToolDef] | None = None,
) -> SimpleAdapter[Any]:
    import os

    from band.adapters.codex import CodexAdapter, CodexAdapterConfig

    return CodexAdapter(
        config=CodexAdapterConfig(
            model=os.environ.get("CODEX_MODEL", s.llm_models.openai_model),
            cwd=os.environ["CODEX_CWD"],
            custom_section=prompt or "",
        ),
        additional_tools=tools,
        features=features,
    )


@adapter(Adapter.OPENCODE, requires=[Dep.OPENCODE_SERVER])
def _build_opencode(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[CustomToolDef] | None = None,
) -> SimpleAdapter[Any]:
    import os

    from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig

    return OpencodeAdapter(
        config=OpencodeAdapterConfig(
            base_url=os.environ["OPENCODE_BASE_URL"],
            provider_id=os.environ.get("OPENCODE_PROVIDER_ID", "opencode"),
            model_id=os.environ.get("OPENCODE_MODEL_ID", "minimax-m2.5-free"),
            custom_section=prompt or "",
        ),
        additional_tools=tools,
        features=features,
    )


@adapter(Adapter.LETTA, requires=[Dep.LETTA_CLOUD])
def _build_letta(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[CustomToolDef] | None = None,
) -> SimpleAdapter[Any]:
    import os

    from band.adapters.letta import LettaAdapter, LettaAdapterConfig

    _reject_tools(Adapter.LETTA, tools)

    # Only override mcp_server_url when set, so the config default applies for a
    # self-hosted server that doesn't need an externally-reachable MCP endpoint.
    config_kwargs: dict[str, Any] = {
        "base_url": os.environ.get("LETTA_BASE_URL", "https://api.letta.com"),
        "provider_key": os.environ.get("LETTA_API_KEY"),
        "model": os.environ.get("LETTA_MODEL", "openai/gpt-4o-mini"),
        "custom_section": prompt or "",
    }
    mcp_server_url = os.environ.get("MCP_SERVER_URL")
    if mcp_server_url:
        config_kwargs["mcp_server_url"] = mcp_server_url
    return LettaAdapter(config=LettaAdapterConfig(**config_kwargs), features=features)
