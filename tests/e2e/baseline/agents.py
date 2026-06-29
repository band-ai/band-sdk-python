"""``@with_agents`` — declare the running agents a test needs, by typed handle.

A test that wants one or more *specific* adapters (rather than the whole matrix)
declares them on the test::

    @with_agents(Adapter.LANGGRAPH, Adapter.ANTHROPIC)
    async def test_greet(agents, user_ops, reply_capture):
        a, b = agents          # ProvisionedAgent each, already running

    @with_agents(Adapter.ANTHROPIC)
    async def test_recall(agent, ...):
        room = await rm.provision_room(participants=[agent.id])

The decorator does two things so the test body stays clean:

* **auto-gates** — it reads each adapter's requirements from the registry and
  applies ``@requires(...)`` for their union, so the test declares no provider
  keys itself (the gate travels with the adapter choice);
* **carries the choice** — it stamps a marker the ``agent`` / ``agents`` fixtures
  (in conftest) read to build + provision + run + reap each agent.

For the *parametrized* case — run one test across the whole adapter matrix (or a
subset) — use ``@across_adapters(...)``, the sibling that drives the
``provisioned_matrix_agent`` fixture::

    @across_adapters(supports={Capability.MEMORY})
    async def test_l2(provisioned_matrix_agent, ...):
        adapter_id, agent = provisioned_matrix_agent

``Adapter`` is re-exported here so a test imports both from one place.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass

import pytest
from _pytest.mark.structures import ParameterSet

from band.core.types import AdapterFeatures, Capability

from tests.e2e.baseline.requires import requires
from tests.e2e.baseline.toolkit.adapters import Adapter, spec_for, specs

__all__ = [
    "Adapter",
    "AGENTS_MARKER",
    "AgentsRequest",
    "across_adapters",
    "adapter_params",
    "with_agents",
]

# Marker name the agent/agents fixtures resolve. Registered in conftest.
AGENTS_MARKER = "with_agents"


@dataclass(frozen=True)
class AgentsRequest:
    """What ``@with_agents`` asks the fixtures to provision (carried on the marker)."""

    adapters: tuple[Adapter, ...]
    prompt: str | None
    features: AdapterFeatures | None


def with_agents(
    *adapters: Adapter,
    prompt: str | None = None,
    features: AdapterFeatures | None = None,
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """Declare the adapters a test runs; inject them via ``agent`` / ``agents``.

    Applies the union of the adapters' registry requirements as ``@requires`` (so
    the test needs no explicit gate) and records the request on a marker. Pass
    ``prompt`` / ``features`` to steer construction (e.g. enable memory) for all
    of them.
    """
    if not adapters:
        raise ValueError("with_agents() needs at least one Adapter")
    # Union of requirements across the chosen adapters, order-preserved.
    deps = tuple(dict.fromkeys(dep for a in adapters for dep in spec_for(a).requires))
    request = AgentsRequest(adapters=adapters, prompt=prompt, features=features)

    def decorate(fn: Callable[..., object]) -> Callable[..., object]:
        fn = requires(*deps)(fn)
        return getattr(pytest.mark, AGENTS_MARKER)(request)(fn)

    return decorate


def adapter_params(
    include: Collection[str] | None = None,
    *,
    exclude: Collection[str] | None = None,
    supports: Collection[Capability] | None = None,
) -> list[ParameterSet]:
    """One ``pytest.param`` per registered adapter, each gated by its requirements.

    Pick all adapters or a subset by what a test checks: no args = the full matrix;
    ``include={...}`` / ``exclude={...}`` slice by id; ``supports={Capability.MEMORY}``
    keeps only adapters advertising that capability. The ``requires(...)`` marks are
    resolved per-parameter by the conftest gate hook (a missing requirement fails the
    cell). Used directly to parametrize an ``adapter_id`` (then ``build_adapter``),
    or via ``across_adapters`` to drive the ``provisioned_matrix_agent`` fixture.
    """
    return [
        pytest.param(spec.id, marks=requires(*spec.requires), id=str(spec.id))
        for spec in specs(include=include, exclude=exclude, supports=supports)
    ]


def across_adapters(
    include: Collection[str] | None = None,
    *,
    exclude: Collection[str] | None = None,
    supports: Collection[Capability] | None = None,
) -> pytest.MarkDecorator:
    """Run a test across the adapter matrix via the ``provisioned_matrix_agent``
    fixture — the parametrized sibling of ``@with_agents``.

    Sugar over ``@pytest.mark.parametrize(..., indirect=True)``: the test injects one
    running agent per cell as ``provisioned_matrix_agent`` (``(adapter_id,
    ProvisionedAgent)``) and the per-cell ``@requires`` gate rides along. No args =
    the full matrix; narrow with ``include`` / ``exclude`` / ``supports`` (the
    indirect parametrization overrides the fixture's default full-matrix params)::

        @across_adapters(exclude={"crewai", "crewai_flow"})
        async def test_l0(provisioned_matrix_agent, resource_manager, ...):
            adapter_id, agent = provisioned_matrix_agent
    """
    return pytest.mark.parametrize(
        "provisioned_matrix_agent",
        adapter_params(include=include, exclude=exclude, supports=supports),
        indirect=True,
    )
