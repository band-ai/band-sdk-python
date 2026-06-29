"""Fixtures for the baseline testing toolkit.

Config comes from the concern-separated ``BaselineSettings`` (see settings.py),
not the legacy flat ``E2ESettings``. Provisioning (provision/reap) and the other
tools add their fixtures here as they are built.
"""

from __future__ import annotations

import functools
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack

import pytest
from anthropic import AsyncAnthropic
from band_rest import AsyncRestClient

from band.client.streaming import WebSocketClient

from tests.e2e.baseline.agents import (
    AGENTS_MARKER,
    MATRIX_MARKER,
    AgentsRequest,
    MatrixBuild,
)
from tests.e2e.baseline.requires import MARKER, Dep, require_dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.adapters import build_adapter, specs
from tests.e2e.baseline.toolkit.judge import Verdict
from tests.e2e.baseline.toolkit.judge import judge as _judge
from tests.e2e.baseline.toolkit.provisioning import (
    ProvisionedAgent,
    ResourceManager,
    new_run_id,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.capture import reply_capture as _reply_capture
from tests.e2e.helpers import TrackingWebSocketClient


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{MARKER}(deps): declare a baseline test's optional dependencies; the "
        "E2E + Band-key gate is always applied. See requires.py.",
    )
    config.addinivalue_line(
        "markers",
        f"{AGENTS_MARKER}(request): set by @with_agents to declare the adapters a "
        "test runs; resolved by the agent/agents fixtures. See agents.py.",
    )
    config.addinivalue_line(
        "markers",
        f"{MATRIX_MARKER}(build): set by @across_adapters to steer per-cell "
        "construction (prompt/features); resolved by matrix_agent.",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Gate every baseline test, then resolve any ``@requires(...)`` extras.

    The gate is unconditional (all baseline tests are live-e2e): E2E disabled
    -> skip; E2E enabled but BAND_API_KEY_USER missing -> fail (misconfig). The
    toolkit drives the platform as the user and provisions its own agents (with
    per-agent generated keys), so a pre-existing static BAND_API_KEY is not
    required. A test only needs ``@requires(...)`` to declare *additional*
    optional capabilities (e.g. provider keys), which skip when absent.
    """
    settings = BaselineSettings()
    if not settings.e2e_tests_enabled:
        pytest.skip("E2E_TESTS_ENABLED is not true")
    if not settings.credentials.api_key_user:
        pytest.fail("BAND_API_KEY_USER not set (E2E enabled)")
    marker = item.get_closest_marker(MARKER)
    if marker is not None:
        # requires() always wraps deps in a tuple; guard the raw-marker case.
        for dep in marker.args[0] if marker.args else ():
            require_dep(dep, settings)


def _selected_adapter_ids() -> frozenset[str] | None:
    """The lane's allowed adapter ids from ``BAND_E2E_ADAPTERS`` (comma-separated).

    Returns the id set, or ``None`` when the env var is unset — meaning *no*
    filtering: the full matrix runs and fails loudly on any missing requirement,
    as a local run should. This is the single knob a CI lane uses to run only the
    adapters it has the venv + keys/backend for (e.g. the crewai lane sets
    ``crewai,crewai_flow``): it *deselects* the rest at collection so their
    fail-loud requirement never turns the lane red. Adding a backend lane later is
    just another value of this var plus the infra those adapters need.
    """
    raw = os.environ.get("BAND_E2E_ADAPTERS")
    if raw is None:
        return None
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _item_target_adapters(item: pytest.Item) -> frozenset[str]:
    """The adapter ids an item is bound to (empty = adapter-agnostic, always kept).

    A matrix cell carries its id as the ``adapter_id`` callspec param; a
    ``@with_agents`` test carries its adapters on the AGENTS_MARKER. Tests with
    neither (provisioning, user-ops, the registry guard) target no adapter and run
    in every lane.
    """
    callspec = getattr(item, "callspec", None)
    if callspec is not None and "adapter_id" in callspec.params:
        return frozenset({str(callspec.params["adapter_id"])})
    marker = item.get_closest_marker(AGENTS_MARKER)
    if marker is not None:
        request: AgentsRequest = marker.args[0]
        return frozenset(str(adapter) for adapter in request.adapters)
    return frozenset()


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Restrict the run to ``BAND_E2E_ADAPTERS`` when set (the CI-lane knob).

    Deselects every test bound to an adapter outside the selected set — both
    matrix cells and ``@with_agents`` tests — so a lane runs only the adapters it
    has the venv + keys/backend for. Adapter-agnostic tests are always kept. Unset
    leaves collection untouched (full matrix, fail-loud).
    """
    selected = _selected_adapter_ids()
    if selected is None:
        return
    kept: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        targets = _item_target_adapters(item)
        if not targets or targets <= selected:
            kept.append(item)
        else:
            deselected.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept


@pytest.fixture(scope="session")
def baseline_settings() -> BaselineSettings:
    return BaselineSettings()


@pytest.fixture(scope="session")
def baseline_run_id() -> str:
    """Token identifying this session's provisioned resources (for naming/sweep)."""
    return new_run_id()


@pytest.fixture(scope="session")
def baseline_user_client(baseline_settings: BaselineSettings) -> AsyncRestClient:
    """Session-scoped user-authenticated REST client for provisioning."""
    assert baseline_settings.credentials.api_key_user, (
        "BAND_API_KEY_USER is required for provisioning"
    )
    return AsyncRestClient(
        api_key=baseline_settings.credentials.api_key_user,
        base_url=baseline_settings.endpoints.rest_url,
    )


@pytest.fixture
def user_ops(baseline_user_client: AsyncRestClient) -> UserOps:
    """User-operation driver over the session-scoped user REST client.

    Reuses ``baseline_user_client`` (which already requires BAND_API_KEY_USER)
    rather than spinning up a fresh client per test.
    """
    return UserOps(baseline_user_client)


@pytest.fixture
async def judge(
    baseline_settings: BaselineSettings,
) -> AsyncGenerator[Callable[..., Awaitable[Verdict]], None]:
    """LLM judge with the client + model pre-bound; call with criteria/transcript.

    Self-gates on its provider key so any test using it skips cleanly when the
    key is absent — the requirement travels with the fixture. The Anthropic
    client is built once here (and closed on teardown) rather than per verdict.

    Usage::

        verdict = await judge(criteria="...", transcript="...")
    """
    require_dep(Dep.ANTHROPIC, baseline_settings)
    async with AsyncAnthropic(
        api_key=baseline_settings.llm_credentials.anthropic_api_key
    ) as client:
        yield functools.partial(
            _judge, client=client, model=baseline_settings.llm_models.judge_model
        )


@pytest.fixture(scope="session")
async def baseline_ws(
    baseline_settings: BaselineSettings,
) -> AsyncGenerator[TrackingWebSocketClient, None]:
    """User-authenticated WS observer for the wait primitives.

    Connects as the user (not an agent), so it coexists with agents and
    receives the same ``message_created`` events. Session-scoped to avoid
    per-test connect/teardown latency; channels are left on teardown.
    """
    assert baseline_settings.credentials.api_key_user, (
        "BAND_API_KEY_USER is required for the WS observer"
    )
    ws = WebSocketClient(
        ws_url=baseline_settings.endpoints.ws_url,
        api_key=baseline_settings.credentials.api_key_user,
        agent_id=None,  # user connection, not an agent
    )
    async with ws:
        tracking = TrackingWebSocketClient(ws)
        yield tracking
        await tracking.cleanup_channels()


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


@pytest.fixture
def reply_capture(
    baseline_ws: TrackingWebSocketClient,
    baseline_settings: BaselineSettings,
    user_ops: UserOps,
) -> Callable[[str], AbstractAsyncContextManager[ReplyCapture]]:
    """Subscribe-before-send capture with the WS observer + E2E_TIMEOUT pre-bound.

    Hides ``baseline_ws`` from tests; use as ``async with reply_capture(room_id)``.
    The capture's default wait deadline comes from E2E_TIMEOUT. ``user_ops`` and
    ``settings`` are pre-bound so ``capture.tool_calls()`` / ``events()`` /
    ``memory(agent)`` can read persisted events and agent-scoped memory.
    """
    return functools.partial(
        _reply_capture,
        baseline_ws,
        user_ops=user_ops,
        settings=baseline_settings,
        deadline_s=baseline_settings.e2e_timeout,
    )


@pytest.fixture
async def resource_manager(
    baseline_settings: BaselineSettings,
    baseline_user_client: AsyncRestClient,
    baseline_run_id: str,
) -> AsyncGenerator[ResourceManager, None]:
    """Per-test provision/reap driver.

    Teardown force-deletes everything provisioned this run, unless
    ``BAND_E2E_AUTOCLEAN`` is false (kept for on-purpose debugging; surviving
    ids are logged by ``reap_all``/``provision_*``).
    """
    resources = ResourceManager(
        user_client=baseline_user_client,
        settings=baseline_settings,
        run_id=baseline_run_id,
    )
    yield resources
    if baseline_settings.run.autoclean:
        await resources.reap_all()


@pytest.fixture(scope="session", autouse=True)
async def orphan_sweep(
    baseline_settings: BaselineSettings,
    baseline_user_client: AsyncRestClient,
    baseline_run_id: str,
) -> None:
    """Reap stale test agents from crashed prior runs, once at session start.

    Prefix-guarded and age-guarded (see ``ResourceManager.sweep_orphans``), so
    it never deletes a non-test agent or a concurrent run's fresh resources.
    No-op when ``BAND_E2E_ORPHAN_SWEEP`` is false.
    """
    if not baseline_settings.run.orphan_sweep:
        return
    resources = ResourceManager(
        user_client=baseline_user_client,
        settings=baseline_settings,
        run_id=baseline_run_id,
    )
    await resources.sweep_orphans()
