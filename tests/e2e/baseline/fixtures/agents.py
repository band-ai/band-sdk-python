"""Agent-provisioning fixtures: the ``@with_agents`` set (``agents``/``agent``)
and the matrix cell (``adapter_id``/``matrix_agent``)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack

import pytest

from tests.e2e.baseline.agents import (
    AGENTS_MARKER,
    MATRIX_MARKER,
    AgentsRequest,
    MatrixBuild,
)
from tests.e2e.baseline.requires import requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.adapters import build_adapter, specs
from tests.e2e.baseline.toolkit.provisioning import (
    ProvisionedAgent,
    ResourceManager,
    running_provisioned_agent,
)

__all__ = ["adapter_id", "agent", "agents", "matrix_agent"]


# Shared agent prompt for the demo adapters: short replies via the tool.
_SHORT_PROMPT = (
    "Keep responses to one short sentence. Always reply using band_send_message."
)


@pytest.fixture
async def agents(
    request: pytest.FixtureRequest,
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
) -> AsyncGenerator[list[ProvisionedAgent], None]:
    """The running agents declared by ``@with_agents(...)``, in declared order.

    Builds each adapter via the registry (steered by the decorator's
    ``prompt``/``features``, defaulting to the short prompt), runs each through
    ``running_provisioned_agent`` (reaped by ``resource_manager`` teardown), and
    yields the ``ProvisionedAgent`` records. Use ``agent`` for the single case.

    Each slot gets an index-suffixed label so the same framework can appear more
    than once (e.g. ``@with_agents(Adapter.ANTHROPIC, Adapter.ANTHROPIC)`` for two
    same-type agents in one room) without provisioned-name collisions.
    """
    marker = request.node.get_closest_marker(AGENTS_MARKER)
    if marker is None:
        raise pytest.UsageError(
            "the `agents`/`agent` fixture requires the @with_agents(...) decorator"
        )
    req: AgentsRequest = marker.args[0]
    prompt = req.prompt if req.prompt is not None else _SHORT_PROMPT
    async with AsyncExitStack() as stack:
        provisioned = [
            await stack.enter_async_context(
                running_provisioned_agent(
                    build_adapter(
                        name,
                        baseline_settings,
                        prompt=prompt,
                        features=req.features,
                        tools=req.tools,
                    ),
                    resource_manager,
                    label=f"{name}-{slot}",
                )
            )
            for slot, name in enumerate(req.adapters)
        ]
        yield provisioned


@pytest.fixture
async def agent(agents: list[ProvisionedAgent]) -> ProvisionedAgent:
    """The single running agent declared by ``@with_agents(OneAdapter)``."""
    if len(agents) != 1:
        raise pytest.UsageError(
            f"`agent` needs exactly one adapter in @with_agents(...); got "
            f"{len(agents)} — use `agents` for multiple."
        )
    return agents[0]


# The full adapter matrix as fixture params: one cell per registered adapter, each
# carrying its ``@requires`` marks so a missing requirement fails that cell. Built
# once at import (registration happens when ``toolkit.adapters`` is imported above).
_MATRIX_PARAMS = [
    pytest.param(spec.id, marks=requires(*spec.requires), id=str(spec.id))
    for spec in specs()
]


@pytest.fixture(params=_MATRIX_PARAMS)
def adapter_id(request: pytest.FixtureRequest) -> str:
    """The current matrix cell's adapter id.

    Parametrized across the whole registry by default (each cell carrying its
    ``@requires`` gate), so requesting this — or ``matrix_agent``, which depends on
    it — fans a test across the matrix. ``@across_adapters(...)`` overrides the set;
    construction-only tests parametrize it directly.
    """
    return request.param


@pytest.fixture
async def matrix_agent(
    request: pytest.FixtureRequest,
    adapter_id: str,
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
) -> AsyncGenerator[ProvisionedAgent, None]:
    """The running provisioned agent for the current matrix cell.

    Built from ``adapter_id`` via the registry and run for the test; request
    ``adapter_id`` alongside it when a test also needs the id. ``@across_adapters``
    may steer construction via the ``MatrixBuild`` marker (``prompt`` / ``features``
    — e.g. enable memory), else a short default prompt and no features. Reaping is
    owned by ``resource_manager`` teardown.
    """
    marker = request.node.get_closest_marker(MATRIX_MARKER)
    build: MatrixBuild | None = marker.args[0] if marker else None
    prompt = build.prompt if build and build.prompt is not None else _SHORT_PROMPT
    features = build.features if build else None
    tools = build.tools if build else None
    adapter = build_adapter(
        adapter_id, baseline_settings, prompt=prompt, features=features, tools=tools
    )
    async with running_provisioned_agent(
        adapter, resource_manager, label=adapter_id
    ) as provisioned:
        yield provisioned
