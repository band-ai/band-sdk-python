"""Adapter discovery + generic construction for the baseline matrix (pytest-free).

This is the one place that knows which framework adapters exist and how to build a
ready-to-run instance of each. Tests never hard-code an adapter list: they iterate
the registry, so L0-L4 scenarios and the smokes are written once and run across the
whole matrix.

Adding a framework
------------------
Two edits: add an ``Adapter`` enum member here (value == the module name) and a
decorated builder in ``builders`` (imported at the bottom of this module for its
registration side-effect)::

    class Adapter(StrEnum):
        ...
        MYFRAMEWORK = "myframework"

    # in builders.py:
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
Heavy/optional framework imports live **inside** each builder so importing the
builders module (which registration triggers) never pulls in an absent dependency.

The CI-lane partition + workflow-drift guards that build on this registry live in
``ci_lanes`` (``adapter_lane`` stays here because ``specs``'s ``lane=`` filter needs
it, and ``ci_lanes`` imports it).
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
    DEFAULT_LANE,
    Dep,
    Lane,
    dep_lane,
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
    COPILOT_SDK = "copilot_sdk"
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
    """A registered adapter: its id, requirements, capabilities, and builder.

    ``e2e_pending`` marks an adapter that is registered (so it still defines its CI
    lane via ``ci_lanes``) but has no live E2E coverage yet: ``specs()`` excludes it
    by default, so the matrix (``adapter_params``) runs no cells for it. It does NOT
    gate ``@with_adapters`` (that resolves any registered adapter) — a pending adapter
    simply has no ``@with_adapters`` tests written for it. Use it to stand up a lane
    ahead of its tests.

    ``runs_tool_loop`` marks an adapter that runs an LLM tool loop able to invoke a
    translated local ``ToolSpec`` and emit observable ``tool_call`` events — the
    precondition for a custom-tool scenario. It is deliberately *decoupled* from
    ``supports`` (platform memory/contacts): an external coding-agent backend could
    run custom tools yet advertise no platform capabilities, so this is its own
    axis rather than ``bool(supports)``. Adapters that return a terminal result
    (crewai_flow) or delegate tools to an external process / MCP server (codex,
    opencode, letta) set it ``False`` and are excluded from the tool-loop matrix
    (``specs(runs_tool_loop=True)``); flip one to ``True`` the day it is proven.
    """

    id: Adapter
    requires: tuple[Dep, ...]
    supports: frozenset[Capability]
    build: AdapterBuilder = field(compare=False)
    e2e_pending: bool = False
    runs_tool_loop: bool = True


# Keyed by the adapter's string id (== ``Adapter`` value; ``Adapter`` is a str
# subclass, so both ``Adapter`` members and plain ids look up transparently).
_REGISTRY: dict[str, AdapterSpec] = {}


def adapter(
    name: Adapter,
    *,
    requires: Iterable[Dep] = (),
    supports: Iterable[Capability] = (),
    e2e_pending: bool = False,
    runs_tool_loop: bool = True,
) -> Callable[[AdapterBuilder], AdapterBuilder]:
    """Register ``name``'s builder in the matrix registry.

    The decorated function keeps its identity (it is returned unchanged) so it can
    also be called directly. Registering a duplicate is a programming error.
    ``e2e_pending=True`` keeps the adapter's CI lane defined but runs no cells for
    it (no live E2E yet). ``runs_tool_loop=False`` excludes the adapter from the
    custom-tool matrix (see ``AdapterSpec``).
    """

    def register(build: AdapterBuilder) -> AdapterBuilder:
        if name in _REGISTRY:
            raise ValueError(f"adapter {name!r} is already registered")
        _REGISTRY[name] = AdapterSpec(
            id=name,
            requires=tuple(requires),
            supports=frozenset(supports),
            build=build,
            e2e_pending=e2e_pending,
            runs_tool_loop=runs_tool_loop,
        )
        return build

    return register


def spec_for(name: Adapter) -> AdapterSpec:
    """The registered spec for ``name`` (raises ``KeyError`` if unregistered)."""
    return _REGISTRY[name]


def _custom_tool_defs(tools: list[ToolSpec] | None) -> list[CustomToolDef] | None:
    """ToolSpecs as band ``CustomToolDef``s for the tool-loop adapters (or None)."""
    return [t.as_custom_tool_def() for t in tools] if tools else None


def _reject_tools(adapter_id: Adapter, tools: list[ToolSpec] | None) -> None:
    """Fail loudly when custom tools are asked of an adapter that can't take them.

    Letta exposes tools via its MCP server, so it can't take a locally-defined
    ``ToolSpec``. Consistent with the toolkit's fail-loudly rule, reject rather
    than silently drop the tools a test requested (which would be a false green).
    """
    if tools:
        raise ValueError(
            f"the {adapter_id.value} adapter does not support custom tools "
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


def adapter_lane(spec: AdapterSpec) -> Lane:
    """The CI lane an adapter runs in: the unique non-default lane among its deps.

    An adapter has at most one lane-defining requirement (lanes are mutually
    exclusive -- a different venv or a different backend), so its lane is that
    dep's lane, else the shared default lane. Two distinct non-default lanes would
    be unsatisfiable in one job and is a configuration error. The lane partition
    that consumes this (``ci_lanes`` + workflow-drift guards) lives in ``ci_lanes``;
    this stays here because ``specs``'s ``lane=`` filter needs it.
    """
    lanes = {dep_lane(dep) for dep in spec.requires} - {DEFAULT_LANE}
    if len(lanes) > 1:
        raise ValueError(
            f"adapter {spec.id!r} requires conflicting lanes {sorted(lanes)}; "
            "an adapter can live in only one lane"
        )
    return next(iter(lanes), DEFAULT_LANE)


# =============================================================================
# Query + construction
# =============================================================================


def specs(
    *,
    include: Collection[Adapter] | None = None,
    exclude: Collection[Adapter] | None = None,
    supports: Collection[Capability] | None = None,
    without: Collection[Capability] | None = None,
    runs_tool_loop: bool | None = None,
    lane: Lane | None = None,
    include_pending: bool = False,
) -> list[AdapterSpec]:
    """The registered specs, optionally narrowed.

    ``include`` keeps only those ids; ``exclude`` drops those ids; ``supports``
    keeps only adapters advertising *all* the given capabilities (e.g.
    ``supports={Capability.MEMORY}``); ``without`` keeps only adapters advertising
    *none* of them (the complement, e.g. ``without={Capability.MEMORY}`` for the
    non-memory adapters). ``supports`` and ``without`` are disjoint complementary
    filters. ``runs_tool_loop=True`` keeps only the custom-tool-capable adapters
    (``False`` the complement); see ``AdapterSpec.runs_tool_loop``. ``lane`` keeps
    only adapters whose derived home lane is ``lane`` (see ``adapter_lane``).
    ``e2e_pending`` adapters are excluded unless ``include_pending`` (they define a
    CI lane but run no cells). Stable id order.
    """
    wanted = frozenset(supports or ())
    unwanted = frozenset(without or ())
    chosen = [
        spec
        for adapter_id, spec in sorted(_REGISTRY.items())
        if (include is None or adapter_id in include)
        and (exclude is None or adapter_id not in exclude)
        and (include_pending or not spec.e2e_pending)
        and wanted.issubset(spec.supports)
        and spec.supports.isdisjoint(unwanted)
        and (runs_tool_loop is None or spec.runs_tool_loop == runs_tool_loop)
        and (lane is None or adapter_lane(spec) == lane)
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


# Import the builders for their ``@adapter`` registration side-effect. Deferred to
# the bottom so every name the builders import from this module (``adapter``,
# ``Adapter``, ``_custom_tool_defs``, ``_reject_tools``) is already defined — this is
# what keeps the ``adapters`` <-> ``builders`` cycle resolvable. Nothing here uses the
# module object; the import exists only to populate ``_REGISTRY`` before ``specs`` /
# ``build_adapter`` are called.
from tests.e2e.baseline.toolkit import builders as _builders  # noqa: E402,F401
