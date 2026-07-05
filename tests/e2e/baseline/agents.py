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
from typing import ClassVar, Self

import pytest
from _pytest.mark.structures import ParameterSet

from band.core.types import AdapterFeatures, Capability

from tests.e2e.baseline.requires import requires
from tests.e2e.baseline.toolkit.adapters import Adapter, Lane, spec_for, specs
from tests.e2e.baseline.toolkit.tools import ToolSpec

__all__ = [
    "WITH_ADAPTERS_MARKER",
    "PER_ADAPTER_MARKER",
    "LANE_MARKER",
    "Adapter",
    "Lane",
    "WithAdapters",
    "PerAdapter",
    "adapter_params",
    "per_adapter",
    "with_adapters",
    "lane",
]


# Marker names the conftest fixtures resolve. Registered in conftest.
WITH_ADAPTERS_MARKER = "with_adapters"  # read by the agent / agents fixtures
PER_ADAPTER_MARKER = (
    "per_adapter"  # read by the cell / agent / peer fixtures (per-cell steering)
)
# An explicit CI-lane assignment (carries a Lane), read by the schedulability guard
# and lane scoping in lane_selection. The override for a multi-framework test whose
# frameworks would otherwise span more than one home lane (unschedulable by default).
LANE_MARKER = "assigned_lane"


def lane(lane_id: Lane) -> pytest.MarkDecorator:
    """Assign a test to an explicit CI lane, overriding derived home-lane scheduling.

    Only needed for a multi-framework test whose frameworks live in different home
    lanes (which is otherwise a collection error): it names the one lane — whose
    ``uv`` extra must host all the frameworks — the test runs in.
    """
    return getattr(pytest.mark, LANE_MARKER)(lane_id)


class MarkerPayload:
    """A dataclass carried as its topology decorator's single marker arg.

    Subclasses set ``MARKER`` to their marker name; :meth:`from_node` is the one
    validated way to read the payload back off a collected test node — fail-loud with a
    ``UsageError`` if the decorator is missing or the marker is malformed, never a
    downstream ``IndexError`` / ``AttributeError`` far from the cause. That the same
    decorator isn't applied *twice* is enforced separately, at collection, by
    ``agent_wiring.assert_agent_fixtures_wired`` — so ``get_closest_marker`` (one marker)
    is the correct read here.
    """

    MARKER: ClassVar[str]

    @classmethod
    def from_node(cls, node: pytest.Item, *, hint: str | None = None) -> Self:
        mark = node.get_closest_marker(cls.MARKER)
        payload = mark.args[0] if mark is not None and mark.args else None
        if not isinstance(payload, cls):  # missing mark, no args, or wrong type
            raise pytest.UsageError(hint or f"this test requires @{cls.MARKER}(...)")
        return payload


@dataclass(frozen=True)
class WithAdapters(MarkerPayload):
    """What ``@with_adapters`` asks the fixtures to provision (carried on its marker)."""

    MARKER: ClassVar[str] = WITH_ADAPTERS_MARKER

    adapters: tuple[Adapter, ...]
    prompt: str | None
    features: AdapterFeatures | None
    tools: list[ToolSpec] | None


@dataclass(frozen=True)
class PerAdapter(MarkerPayload):
    """Per-cell construction steering for ``@per_adapter`` (carried on its marker)."""

    MARKER: ClassVar[str] = PER_ADAPTER_MARKER

    prompt: str | None
    features: AdapterFeatures | None
    tools: list[ToolSpec] | None
    peer: Adapter | None = None


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
    lane: Lane | None = None,
    peer: Adapter | None = None,
) -> list[ParameterSet]:
    """One ``pytest.param`` per registered adapter (narrowed by the filters), each gated
    by its requirements.

    No args = the full matrix; ``include`` / ``exclude`` slice by id; ``supports`` /
    ``without`` by capability (complementary); ``runs_tool_loop=True`` keeps the
    custom-tool-capable adapters; ``lane`` keeps only a single home lane's adapters.
    Each param carries **one** ``@requires`` mark — the deduped union of the cell's own
    requirements and, when a ``peer`` framework rides along, the peer's. It must be a
    *single* mark: the gate reads it with ``get_closest_marker`` (first mark only), so a
    second stacked mark would be silently dropped. The conftest's ``pytest_runtest_setup``
    gate resolves it when that cell runs (a missing requirement fails the cell). This is
    the parameter source ``@per_adapter`` feeds to the ``adapter_id`` fixture.
    """
    peer_deps = spec_for(peer).requires if peer is not None else ()
    return [
        pytest.param(
            spec.id,
            marks=requires(*dict.fromkeys((*spec.requires, *peer_deps))),
            id=str(spec.id),
        )
        for spec in specs(
            include=include,
            exclude=exclude,
            supports=supports,
            without=without,
            runs_tool_loop=runs_tool_loop,
            lane=lane,
        )
    ]


def per_adapter(
    *adapters: Adapter,
    exclude: Collection[Adapter] | None = None,
    supports: Collection[Capability] | None = None,
    without: Collection[Capability] | None = None,
    runs_tool_loop: bool | None = None,
    lane: Lane | None = None,
    peer: Adapter | None = None,
    prompt: str | None = None,
    features: AdapterFeatures | None = None,
    tools: list[ToolSpec] | None = None,
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """Fan a test across the adapter matrix — one invocation per selected adapter.

    Positional ``*adapters`` is the include set; ``exclude`` / ``supports`` / ``without`` /
    ``runs_tool_loop`` / ``lane`` narrow it (all compose; ``lane`` keeps one home lane's
    adapters). Bare ``@per_adapter()`` is the full matrix, explicitly. Steer per-cell
    construction with ``prompt`` / ``features`` / ``tools`` — the ``cell`` / ``agent`` /
    ``peer`` fixtures carry these as defaults::

        @per_adapter()                                   # full matrix
        @per_adapter(exclude={Adapter.CREWAI})           # all but crewai
        @per_adapter(Adapter.ANTHROPIC, Adapter.AGNO)    # only these
        @per_adapter(supports={Capability.MEMORY})       # by capability
        @per_adapter(lane=Lane.CORE, peer=Adapter.LANGGRAPH)  # each cell + a foreign peer

    ``peer`` names a second, different-framework agent the test drives itself (request
    the ``peer`` fixture for its cell); its requirements fold into each cell's single
    ``@requires`` mark so the peer's key is gated too. Request ``agent`` (managed,
    running) or ``cell`` (drive it yourself); the per-cell ``@requires`` gate rides on
    the parameters.
    """
    include = frozenset(adapters) or None
    if peer is not None:
        if peer not in {s.id for s in specs(include_pending=True)}:
            raise ValueError(f"@per_adapter(peer={peer!r}) is not a registered adapter")
        if peer not in {s.id for s in specs()}:
            # A pending adapter defines a lane but runs no cells (no builder-backed cell),
            # so it can't be provisioned as a peer — fail at decoration, not at runtime.
            raise ValueError(
                f"@per_adapter(peer={peer!r}) is a pending adapter (runs no cells); "
                "pick a live adapter as the peer"
            )
    params = adapter_params(
        include=include,
        exclude=exclude,
        supports=supports,
        without=without,
        runs_tool_loop=runs_tool_loop,
        lane=lane,
        peer=peer,
    )
    # Fail loud rather than let an empty parametrize skip silently (a mis-specified
    # filter or registry drift). A bare @per_adapter() is never empty.
    if not params:
        raise ValueError(
            "@per_adapter selected no adapters "
            f"(include={sorted(map(str, include)) if include else None}, "
            f"exclude={exclude}, supports={supports}, without={without}, "
            f"runs_tool_loop={runs_tool_loop}, lane={lane}); widen the filter or fix "
            "the registry drift"
        )
    build = PerAdapter(prompt=prompt, features=features, tools=tools, peer=peer)

    def decorate(fn: Callable[..., object]) -> Callable[..., object]:
        fn = pytest.mark.parametrize("adapter_id", params, indirect=True)(fn)
        # `agent` / `cell` reach `adapter_id` only dynamically (getfixturevalue), which
        # the static fixture closure can't see — so indirect parametrize alone errors at
        # collection ("function uses no fixture 'adapter_id'"). usefixtures pins the
        # parametrized name into the closure for every @per_adapter test.
        fn = pytest.mark.usefixtures("adapter_id")(fn)
        return getattr(pytest.mark, PER_ADAPTER_MARKER)(build)(fn)

    return decorate
