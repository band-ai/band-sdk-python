"""``@per_adapter`` / ``@with_adapters`` — declare the agents a test runs, by typed handle.

Two topologies, one vocabulary:

* ``@per_adapter(...)`` **fans** a test across the adapter matrix (or a filtered subset):
  one invocation per selected adapter. Request ``agent`` (a managed, running
  ``ProvisionedAgent``) or ``cell`` (an ``AdapterCell`` you drive yourself, for
  construction / reboot / rehydration lifecycles); the cell id is ``agent.adapter_id`` /
  ``cell.adapter_id``::

      @per_adapter(supports={Capability.MEMORY}, features=memory_features())
      async def test_recall(agent): ...          # once per memory-capable adapter

      @per_adapter()                             # the full matrix, explicitly
      def test_build(cell):
          assert isinstance(cell.build(), SimpleAdapter)

* ``@with_adapters(...)`` **groups** a fixed set of named adapters into one invocation
  (one room): request ``agents`` (list) or ``agent`` (the single case)::

      @with_adapters(Adapter.LANGGRAPH, Adapter.ANTHROPIC)
      async def test_collab(agents):
          a, b = agents

Both auto-gate: each reads its adapters' requirements from the registry and applies
``@requires(...)`` for the union, so a test declares no provider keys itself (the gate
travels with the adapter choice). ``Adapter`` is re-exported here so a test imports both
decorators and the handle from one place.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass

import pytest
from _pytest.mark.structures import ParameterSet

from band.core.types import AdapterFeatures, Capability

from tests.e2e.baseline.requires import requires
from tests.e2e.baseline.toolkit.adapters import Adapter, spec_for, specs
from tests.e2e.baseline.toolkit.tools import ToolSpec

__all__ = [
    "WITH_ADAPTERS_MARKER",
    "PER_ADAPTER_MARKER",
    "Adapter",
    "WithAdapters",
    "PerAdapter",
    "adapter_params",
    "per_adapter",
    "with_adapters",
]


# Marker names the conftest fixtures resolve. Registered in conftest.
WITH_ADAPTERS_MARKER = "with_adapters"  # read by the agent / agents fixtures
PER_ADAPTER_MARKER = (
    "per_adapter"  # read by the cell / agent fixtures (per-cell steering)
)


@dataclass(frozen=True)
class WithAdapters:
    """What ``@with_adapters`` asks the fixtures to provision (carried on the marker)."""

    adapters: tuple[Adapter, ...]
    prompt: str | None
    features: AdapterFeatures | None
    tools: list[ToolSpec] | None


@dataclass(frozen=True)
class PerAdapter:
    """Per-cell construction steering for ``@per_adapter`` (carried on the marker)."""

    prompt: str | None
    features: AdapterFeatures | None
    tools: list[ToolSpec] | None


def with_adapters(
    *adapters: Adapter,
    prompt: str | None = None,
    features: AdapterFeatures | None = None,
    tools: list[ToolSpec] | None = None,
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """Declare a fixed set of adapters to run together in one room.

    Injects them via ``agent`` (single) / ``agents`` (list). Applies the union of the
    adapters' registry requirements as ``@requires`` (so the test needs no explicit gate)
    and records the request on a marker. Pass ``prompt`` / ``features`` / ``tools`` to
    steer construction for all of them.
    """
    if not adapters:
        raise ValueError("with_adapters() needs at least one Adapter")
    # Union of requirements across the chosen adapters, order-preserved.
    deps = tuple(dict.fromkeys(dep for a in adapters for dep in spec_for(a).requires))
    request = WithAdapters(
        adapters=adapters, prompt=prompt, features=features, tools=tools
    )

    def decorate(fn: Callable[..., object]) -> Callable[..., object]:
        fn = requires(*deps)(fn)
        return getattr(pytest.mark, WITH_ADAPTERS_MARKER)(request)(fn)

    return decorate


def adapter_params(
    include: Collection[Adapter] | None = None,
    *,
    exclude: Collection[Adapter] | None = None,
    supports: Collection[Capability] | None = None,
    without: Collection[Capability] | None = None,
    runs_tool_loop: bool | None = None,
) -> list[ParameterSet]:
    """One ``pytest.param`` per registered adapter (narrowed by the filters), each gated
    by its requirements.

    No args = the full matrix; ``include`` / ``exclude`` slice by id; ``supports`` /
    ``without`` by capability (complementary); ``runs_tool_loop=True`` keeps the
    custom-tool-capable adapters. Each param carries its adapter's ``@requires`` marks;
    the conftest's ``pytest_runtest_setup`` gate resolves them when that cell runs (a
    missing requirement fails the cell). This is the parameter source ``@per_adapter``
    feeds to the ``adapter_id`` fixture.
    """
    return [
        pytest.param(spec.id, marks=requires(*spec.requires), id=str(spec.id))
        for spec in specs(
            include=include,
            exclude=exclude,
            supports=supports,
            without=without,
            runs_tool_loop=runs_tool_loop,
        )
    ]


def per_adapter(
    *adapters: Adapter,
    exclude: Collection[Adapter] | None = None,
    supports: Collection[Capability] | None = None,
    without: Collection[Capability] | None = None,
    runs_tool_loop: bool | None = None,
    prompt: str | None = None,
    features: AdapterFeatures | None = None,
    tools: list[ToolSpec] | None = None,
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """Fan a test across the adapter matrix — one invocation per selected adapter.

    Positional ``*adapters`` is the include set; ``exclude`` / ``supports`` / ``without`` /
    ``runs_tool_loop`` narrow it (all compose). Bare ``@per_adapter()`` is the full
    matrix, explicitly. Steer per-cell construction with ``prompt`` / ``features`` /
    ``tools`` — the ``cell`` / ``agent`` fixtures carry these as defaults::

        @per_adapter()                                   # full matrix
        @per_adapter(exclude={Adapter.CREWAI})           # all but crewai
        @per_adapter(Adapter.ANTHROPIC, Adapter.AGNO)    # only these
        @per_adapter(supports={Capability.MEMORY})       # by capability

    Request ``agent`` (managed, running) or ``cell`` (drive it yourself); the per-cell
    ``@requires`` gate rides on the parameters.
    """
    include = frozenset(adapters) or None
    params = adapter_params(
        include=include,
        exclude=exclude,
        supports=supports,
        without=without,
        runs_tool_loop=runs_tool_loop,
    )
    # Fail loud rather than let an empty parametrize skip silently (a mis-specified
    # filter or registry drift). A bare @per_adapter() is never empty.
    if not params:
        raise ValueError(
            "@per_adapter selected no adapters "
            f"(include={sorted(map(str, include)) if include else None}, "
            f"exclude={exclude}, supports={supports}, without={without}, "
            f"runs_tool_loop={runs_tool_loop}); widen the filter or fix the registry drift"
        )
    build = PerAdapter(prompt=prompt, features=features, tools=tools)

    def decorate(fn: Callable[..., object]) -> Callable[..., object]:
        fn = pytest.mark.parametrize("adapter_id", params, indirect=True)(fn)
        # `agent` / `cell` reach `adapter_id` only dynamically (getfixturevalue), which
        # the static fixture closure can't see — so indirect parametrize alone errors at
        # collection ("function uses no fixture 'adapter_id'"). usefixtures pins the
        # parametrized name into the closure for every @per_adapter test.
        fn = pytest.mark.usefixtures("adapter_id")(fn)
        return getattr(pytest.mark, PER_ADAPTER_MARKER)(build)(fn)

    return decorate
