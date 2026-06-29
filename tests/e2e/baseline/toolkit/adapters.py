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
    def _build_myframework(settings, *, prompt, features, tools=None):
        from band.adapters.myframework import MyframeworkAdapter
        return MyframeworkAdapter(
            model=settings.llm_models.openai_model,
            custom_section=prompt,
            additional_tools=_custom_tool_defs(tools),
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
from tests.e2e.baseline.toolkit.tools import ToolSpec

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.requirements import (
    DEFAULT_EXTRA,
    Dep,
    DepKind,
    dep_extra,
    dep_kind,
    validate_dep_tables,
)


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


def _custom_tool_defs(tools: list[ToolSpec] | None) -> list[CustomToolDef] | None:
    """ToolSpecs as band ``CustomToolDef``s for the tool-loop adapters (or None)."""
    return [t.as_custom_tool_def() for t in tools] if tools else None


def _reject_tools(adapter: Adapter, tools: list[ToolSpec] | None) -> None:
    """Fail loudly when custom tools are asked of an adapter that can't take them.

    Letta exposes tools via its MCP server, so it can't take a locally-defined
    ``ToolSpec``. Consistent with the toolkit's fail-loudly rule, reject rather
    than silently drop the tools a test requested (which would be a false green).
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
NON_AGENT_ADAPTERS: frozenset[str] = frozenset(
    {"a2a", "a2a_gateway", "acp", "slack", "parlant"}
)


def discovered_agent_ids() -> set[str]:
    """The LLM-agent adapter ids present in ``src/band/adapters/`` (minus NON_AGENT_ADAPTERS).

    Scans module *names* only (``pkgutil`` does not import them), so an adapter
    whose optional dependency is absent is still discovered and never breaks
    collection.
    """
    names = {
        module.name
        for module in pkgutil.iter_modules(band.adapters.__path__)
        if not module.name.startswith("_")
    }
    return names - NON_AGENT_ADAPTERS


def registered_ids() -> set[str]:
    return set(_REGISTRY)


def assert_registry_covers_discovered() -> None:
    """Fail loudly on any drift between enum, registry, and discovered modules.

    Three sources must agree exactly: the ``Adapter`` enum, the ``@adapter``
    registry, and the agent modules under ``src/band/adapters/`` (minus NON_AGENT_ADAPTERS).
    A new framework with no enum member / no builder, or a stale entry (an enum
    member or builder with no module, or a module that should be in NON_AGENT_ADAPTERS) all
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
            f"  enum/registry with no module (stale or should be NON_AGENT_ADAPTERS): "
            f"{sorted((enum_values | registered) - discovered)}"
        )


# =============================================================================
# CI lane partition: derived from each adapter's requirements
# =============================================================================
#
# CI cannot run one venv green across the whole fail-loud matrix (crewai conflicts
# with the default lane's deps; codex/opencode/letta need external backends). The
# partition is *derived* from each spec's ``requires`` -- never a hand-maintained
# list -- so a newly-registered adapter lands in its lane for free and the guard
# below fails loudly if it lands nowhere.


def adapter_extra(spec: AdapterSpec) -> str:
    """The single ``uv`` extra an adapter's venv needs.

    An adapter has at most one VENV requirement (the lanes are mutually-exclusive
    extras), so its extra is that dep's extra, else the default lane's. Two distinct
    VENV deps would be unsatisfiable in one venv and is a configuration error.
    """
    venv_extras = {
        dep_extra(dep) for dep in spec.requires if dep_kind(dep) is DepKind.VENV
    }
    if len(venv_extras) > 1:
        raise ValueError(
            f"adapter {spec.id!r} requires conflicting venv extras "
            f"{sorted(venv_extras)}; an adapter can live in only one lane"
        )
    return next(iter(venv_extras), DEFAULT_EXTRA)


def is_infra_adapter(spec: AdapterSpec) -> bool:
    """True if any requirement is an external backend (no CI lane yet)."""
    return any(dep_kind(dep) is DepKind.INFRA for dep in spec.requires)


def ci_lanes() -> dict[str, list[Adapter]]:
    """Map each ``uv`` extra -> the CI-auto-runnable adapters that live in it.

    CI-auto-runnable = no INFRA requirement (every non-VENV dep is a provider key a
    secret can satisfy); infra adapters are excluded (see ``infra_adapters``). The
    ``DEFAULT_EXTRA`` key is always present (even if empty) so the default lane has
    a well-defined set. Stable id order within each lane. This is what the CI
    workflow consumes to fan one job per lane.
    """
    lanes: dict[str, list[Adapter]] = {DEFAULT_EXTRA: []}
    for spec in specs():  # stable id order
        if not is_infra_adapter(spec):
            lanes.setdefault(adapter_extra(spec), []).append(spec.id)
    return lanes


def infra_adapters() -> list[Adapter]:
    """Adapters gated on an external backend (in no CI lane until one is wired)."""
    return [spec.id for spec in specs() if is_infra_adapter(spec)]


def assert_every_adapter_has_a_ci_home() -> None:
    """Fail loudly unless every registered adapter is placed for CI.

    Partner to ``assert_registry_covers_discovered``: that guard ensures a new
    adapter is *registered*; this one ensures it is *placed* -- ``ci_lanes()`` and
    ``infra_adapters()`` together cover the whole registry. Building those also
    validates the Dep table and surfaces a mis-specified adapter early (an
    unspecified ``Dep`` raises in ``dep_kind``; two VENV deps raise in
    ``adapter_extra``), so a new adapter cannot silently vanish from CI.
    """
    validate_dep_tables()
    placed = {a for ids in ci_lanes().values() for a in ids} | set(infra_adapters())
    unplaced = {spec.id for spec in specs()} - placed
    if unplaced:
        raise AssertionError(
            "adapters not placed in any CI lane or infra (ci_lanes/infra_adapters "
            f"must cover the registry): {sorted(str(a) for a in unplaced)}"
        )


# =============================================================================
# Query + construction
# =============================================================================


def specs(
    *,
    include: Collection[Adapter] | None = None,
    exclude: Collection[Adapter] | None = None,
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
    tools: list[ToolSpec] | None = None,
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
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.anthropic import AnthropicAdapter

    return AnthropicAdapter(
        model=s.llm_models.anthropic_model,
        provider_key=s.llm_credentials.anthropic_api_key or None,
        prompt=prompt,
        additional_tools=_custom_tool_defs(tools),
        features=features,
    )


@adapter(Adapter.CLAUDE_SDK, requires=[Dep.ANTHROPIC], supports=_LLM_TOOL_LOOP)
def _build_claude_sdk(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.claude_sdk import ClaudeSDKAdapter

    return ClaudeSDKAdapter(
        model=s.llm_models.anthropic_model,
        custom_section=prompt,
        additional_tools=_custom_tool_defs(tools),
        features=features,
    )


@adapter(Adapter.LANGGRAPH, requires=[Dep.OPENAI], supports=_LLM_TOOL_LOOP)
def _build_langgraph(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
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
        additional_tools=_custom_tool_defs(tools),
        features=features,
    )


@adapter(Adapter.PYDANTIC_AI, requires=[Dep.OPENAI], supports=_LLM_TOOL_LOOP)
def _build_pydantic_ai(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from pydantic_ai import RunContext

    from band.adapters.pydantic_ai import PydanticAIAdapter

    # pydantic-ai takes native callables with a RunContext-first signature.
    native = (
        [t.as_callable(ctx_annotation=RunContext) for t in tools] if tools else None
    )
    return PydanticAIAdapter(
        model=f"openai:{s.llm_models.openai_model}",
        custom_section=prompt,
        additional_tools=native,
        features=features,
    )


@adapter(Adapter.GEMINI, requires=[Dep.GOOGLE], supports=_LLM_TOOL_LOOP)
def _build_gemini(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.gemini import GeminiAdapter

    return GeminiAdapter(
        model=s.llm_models.gemini_model,
        provider_key=s.llm_credentials.google_api_key or None,
        prompt=prompt,
        additional_tools=_custom_tool_defs(tools),
        features=features,
    )


@adapter(Adapter.GOOGLE_ADK, requires=[Dep.GOOGLE], supports=_LLM_TOOL_LOOP)
def _build_google_adk(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.google_adk import GoogleADKAdapter

    # google-adk reads the provider key / Vertex config from the environment.
    return GoogleADKAdapter(
        model=s.llm_models.gemini_model,
        custom_section=prompt,
        additional_tools=_custom_tool_defs(tools),
        features=features,
    )


@adapter(Adapter.CREWAI, requires=[Dep.OPENAI, Dep.CREWAI], supports=_LLM_TOOL_LOOP)
def _build_crewai(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.crewai import CrewAIAdapter

    return CrewAIAdapter(
        model=s.llm_models.openai_model,
        role="Test Assistant",
        goal="Help users with simple tasks for testing.",
        backstory="A test agent for E2E validation.",
        custom_section=prompt,
        additional_tools=_custom_tool_defs(tools),
        features=features,
    )


@adapter(Adapter.AGNO, requires=[Dep.ANTHROPIC], supports=_LLM_TOOL_LOOP)
def _build_agno(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    # Agno bridges a user-built agent, so steering goes into its instructions.
    # Use the Anthropic model: small models refuse the suite's crafted prompts as
    # injection, so the matrix relies on E2E_ANTHROPIC_MODEL being a capable model.
    from agno.agent import Agent as AgnoAgent
    from agno.models.anthropic import Claude

    from band.adapters.agno import AgnoAdapter

    # agno tools are plain callables on the agent; the band adapter captures them
    # and re-offers them alongside the platform tools each run.
    native = [t.as_callable() for t in tools] if tools else None
    return AgnoAdapter(
        AgnoAgent(
            model=Claude(id=s.llm_models.anthropic_model),
            instructions=prompt,
            tools=native,
        ),
        features=features,
    )


@adapter(Adapter.CREWAI_FLOW, requires=[Dep.CREWAI])
def _build_crewai_flow(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
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
        flow_factory=_E2EFlow,
        additional_tools=_custom_tool_defs(tools),
        features=features,
    )


@adapter(Adapter.CODEX, requires=[Dep.CODEX_CLI, Dep.CODEX_CWD])
def _build_codex(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    import os

    from band.adapters.codex import CodexAdapter, CodexAdapterConfig

    return CodexAdapter(
        config=CodexAdapterConfig(
            model=os.environ.get("CODEX_MODEL", s.llm_models.openai_model),
            cwd=os.environ["CODEX_CWD"],
            custom_section=prompt or "",
        ),
        additional_tools=_custom_tool_defs(tools),
        features=features,
    )


@adapter(Adapter.OPENCODE, requires=[Dep.OPENCODE_SERVER])
def _build_opencode(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
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
        additional_tools=_custom_tool_defs(tools),
        features=features,
    )


@adapter(Adapter.LETTA, requires=[Dep.LETTA_CLOUD])
def _build_letta(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
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
