"""Fixtures for the baseline testing toolkit.

Config comes from the concern-separated ``BaselineSettings`` (see settings.py),
not the legacy flat ``E2ESettings``. Provisioning (provision/reap) and the other
tools add their fixtures here as they are built.
"""

from __future__ import annotations

import functools
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager

import pytest
from anthropic import AsyncAnthropic
from band_rest import AsyncRestClient

from band.client.streaming import WebSocketClient
from band.core.simple_adapter import SimpleAdapter

from tests.e2e.baseline.requires import MARKER, Dep, require_dep
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.judge import Verdict
from tests.e2e.baseline.toolkit.judge import judge as _judge
from tests.e2e.baseline.toolkit.provisioning import ResourceManager, new_run_id
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


# TODO turn adapter creation into a factory or other generic no-glue mechanism.
@pytest.fixture
def langgraph_adapter(baseline_settings: BaselineSettings) -> SimpleAdapter:
    """A cheap LangGraph agent built from settings (OpenAI-backed).

    Self-gates on its provider key, so a test using it skips when OPENAI_API_KEY
    is absent — the requirement travels with the fixture.
    """
    require_dep(Dep.OPENAI, baseline_settings)
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import MemorySaver

    from band.adapters.langgraph import LangGraphAdapter

    return LangGraphAdapter(
        llm=ChatOpenAI(
            model=baseline_settings.llm_models.openai_model,
            api_key=baseline_settings.llm_credentials.openai_api_key,
        ),
        checkpointer=MemorySaver(),
        custom_section=_SHORT_PROMPT,
    )


# TODO turn adapter creation into a factory or other generic no-glue mechanism.
@pytest.fixture
def anthropic_adapter(baseline_settings: BaselineSettings) -> SimpleAdapter:
    """A cheap Anthropic agent built from settings.

    Self-gates on its provider key, so a test using it skips when
    ANTHROPIC_API_KEY is absent — the requirement travels with the fixture.
    """
    require_dep(Dep.ANTHROPIC, baseline_settings)
    from band.adapters.anthropic import AnthropicAdapter

    return AnthropicAdapter(
        model=baseline_settings.llm_models.anthropic_model,
        provider_key=baseline_settings.llm_credentials.anthropic_api_key,
        prompt=_SHORT_PROMPT,
    )


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
