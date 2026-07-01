"""Agent fixtures for the two topologies.

* ``@per_adapter`` (fan) → ``adapter_id`` (the cell id), ``cell`` (an ``AdapterCell`` to
  drive yourself), and ``agent`` (a managed, running ``ProvisionedAgent`` — sugar over
  ``cell.running()``).
* ``@with_adapters`` (group) → ``agent`` (single) / ``agents`` (list), each a running
  ``ProvisionedAgent``.

Every path is explicit: requesting any of these without the matching decorator is a
``UsageError`` (belt-and-suspenders behind the collection-time wiring guard). Both
topologies build through ``AdapterCell`` so construction + run wiring lives in one place.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

import pytest

from band.core.types import AdapterFeatures

from tests.e2e.baseline.agents import (
    WITH_ADAPTERS_MARKER,
    PER_ADAPTER_MARKER,
    Adapter,
    WithAdapters,
    PerAdapter,
)
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ProvisionedAgent,
    ResourceManager,
)
from tests.e2e.baseline.toolkit.tools import ToolSpec

__all__ = ["adapter_id", "agent", "agents", "cell", "peer"]


# The demo adapters reply tersely and in-chat unless a test steers otherwise; this is the
# fixture-layer default prompt, applied when a decorator sets no prompt of its own.
_SHORT_PROMPT = "Keep responses to one short sentence. Reply directly in the chat."


def _make_cell(
    adapter_id: str | Adapter,
    settings: BaselineSettings,
    resources: ResourceManager,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None,
) -> AdapterCell:
    """Build an ``AdapterCell`` with the decorator's steering, defaulting the prompt."""
    return AdapterCell(
        adapter_id=str(adapter_id),
        settings=settings,
        resources=resources,
        prompt=prompt if prompt is not None else _SHORT_PROMPT,
        features=features,
        tools=tools,
    )


@asynccontextmanager
async def _running_group_member(
    name: str | Adapter,
    slot: int,
    req: WithAdapters,
    settings: BaselineSettings,
    resources: ResourceManager,
) -> AsyncGenerator[ProvisionedAgent, None]:
    """Run one ``@with_adapters`` member: a cell built from the request's steering,
    slot-labelled so the same framework can repeat in a room without name collisions.

    The single shared path for both ``agent`` (the one-adapter case) and ``agents`` (the
    group), so the two never drift.
    """
    cell = _make_cell(
        name,
        settings,
        resources,
        prompt=req.prompt,
        features=req.features,
        tools=req.tools,
    )
    async with cell.running(label=f"{name}-{slot}") as running:
        yield running


@pytest.fixture
def adapter_id(request: pytest.FixtureRequest) -> str:
    """The current ``@per_adapter`` cell's adapter id (normalized to ``str``).

    Only resolvable under ``@per_adapter``, which parametrizes it indirectly. Requesting
    it (or any matrix fixture) without the decorator is a usage error.
    """
    if not hasattr(request, "param"):
        raise pytest.UsageError(
            "`adapter_id` is set by @per_adapter(...); a test cannot request the matrix "
            "fixtures without that decorator."
        )
    return str(request.param)


@pytest.fixture
def cell(
    request: pytest.FixtureRequest,
    adapter_id: str,
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
) -> AdapterCell:
    """The ``AdapterCell`` for the current cell — build / provision / run it yourself.

    Reach for it (instead of ``agent``) when the test owns the lifecycle: a no-provision
    construction check, or a reboot / rehydration scenario. Carries the decorator's
    ``prompt`` / ``features`` / ``tools`` as defaults.
    """
    marker = request.node.get_closest_marker(PER_ADAPTER_MARKER)
    if marker is None:
        raise pytest.UsageError("the `cell` fixture requires @per_adapter(...).")
    each: PerAdapter = marker.args[0]
    return _make_cell(
        adapter_id,
        baseline_settings,
        resource_manager,
        prompt=each.prompt,
        features=each.features,
        tools=each.tools,
    )


@pytest.fixture
def peer(
    request: pytest.FixtureRequest,
    adapter_id: str,
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
) -> AdapterCell:
    """The foreign-framework peer ``AdapterCell`` declared by ``@per_adapter(peer=...)``.

    A second, *different-framework* agent the test drives itself (provision + run_as),
    for cross-framework scenarios (e.g. A rehydrates a message B authored). Built like
    ``cell`` — inheriting the decorator's ``prompt`` / ``features`` / ``tools`` — but for
    the declared peer adapter. Fails loud if the peer equals the current cell (that would
    be same-framework, not cross): exclude the peer from the fan, or pick another.
    """
    marker = request.node.get_closest_marker(PER_ADAPTER_MARKER)
    if marker is None:
        raise pytest.UsageError("the `peer` fixture requires @per_adapter(peer=...).")
    each: PerAdapter = marker.args[0]
    if each.peer is None:
        raise pytest.UsageError(
            "the `peer` fixture requires @per_adapter(peer=...); no peer= was declared."
        )
    if str(each.peer) == adapter_id:
        raise pytest.UsageError(
            f"peer {str(each.peer)!r} equals the cell adapter {adapter_id!r} — a peer must "
            "be a different framework; exclude it from the fan or pick another."
        )
    return _make_cell(
        each.peer,
        baseline_settings,
        resource_manager,
        prompt=each.prompt,
        features=each.features,
        tools=each.tools,
    )


@pytest.fixture
async def agent(
    request: pytest.FixtureRequest,
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
) -> AsyncGenerator[ProvisionedAgent, None]:
    """The single running agent — for ``@per_adapter`` (the cell) or ``@with_adapters(One)``.

    Provisioned, run for the test, and reaped by the resource manager. Use ``agents`` for a
    ``@with_adapters`` group, or ``cell`` when a test drives its own lifecycle.
    """
    if request.node.get_closest_marker(PER_ADAPTER_MARKER) is not None:
        # Sugar over the cell (sync fixture; pulled dynamically since `agent` doesn't
        # statically depend on it) — the decorator steering already lives on the cell.
        running_cell: AdapterCell = request.getfixturevalue("cell")
        async with running_cell.running() as running:
            yield running
        return

    marker = request.node.get_closest_marker(WITH_ADAPTERS_MARKER)
    if marker is None:
        raise pytest.UsageError(
            "`agent` requires @per_adapter(...) or @with_adapters(OneAdapter)."
        )
    req: WithAdapters = marker.args[0]
    if len(req.adapters) != 1:
        raise pytest.UsageError(
            f"`agent` needs exactly one adapter in @with_adapters(...); got "
            f"{len(req.adapters)} — use `agents` for multiple."
        )
    async with _running_group_member(
        req.adapters[0], 0, req, baseline_settings, resource_manager
    ) as running:
        yield running


@pytest.fixture
async def agents(
    request: pytest.FixtureRequest,
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
) -> AsyncGenerator[list[ProvisionedAgent], None]:
    """The running agents declared by ``@with_adapters(...)``, in declared order.

    Each slot gets an index-suffixed label so the same framework can appear more than once
    (e.g. two Anthropic agents in one room) without provisioned-name collisions.
    """
    marker = request.node.get_closest_marker(WITH_ADAPTERS_MARKER)
    if marker is None:
        raise pytest.UsageError(
            "the `agents` fixture requires @with_adapters(...); use `agent`/`cell` under "
            "@per_adapter."
        )
    req: WithAdapters = marker.args[0]
    async with AsyncExitStack() as stack:
        provisioned = [
            await stack.enter_async_context(
                _running_group_member(
                    name, slot, req, baseline_settings, resource_manager
                )
            )
            for slot, name in enumerate(req.adapters)
        ]
        yield provisioned
