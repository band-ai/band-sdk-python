"""Fixtures for the baseline testing toolkit.

Config comes from the concern-separated ``BaselineSettings`` (see settings.py),
not the legacy flat ``E2ESettings``. Provisioning (mint/reap) and the other
tools add their fixtures here as they are built.
"""

from __future__ import annotations

import functools
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager

import pytest
from band_rest import AsyncRestClient

from band.client.streaming import WebSocketClient

from tests.e2e.baseline.requires import MARKER, Dep, require_dep
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.judge import Verdict
from tests.e2e.baseline.toolkit.judge import judge as _judge
from tests.e2e.baseline.toolkit.provisioning import ResourceManager, new_run_id
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.waiting import ReplyCapture
from tests.e2e.baseline.toolkit.waiting import reply_capture as _reply_capture
from tests.e2e.helpers import TrackingWebSocketClient


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{MARKER}(deps): declare a baseline test's optional dependencies; the "
        "E2E + Band-key gate is always applied. See requires.py.",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Resolve ``@requires(...)`` before a baseline test runs.

    Always-on gate: E2E disabled -> skip; E2E enabled but a Band key missing ->
    fail (misconfig). Then each declared optional dependency skips or fails per
    its registry disposition.
    """
    marker = item.get_closest_marker(MARKER)
    if marker is None:
        return
    settings = BaselineSettings()
    if not settings.e2e_tests_enabled:
        pytest.skip("E2E_TESTS_ENABLED is not true")
    if not settings.credentials.api_key:
        pytest.fail("BAND_API_KEY not set (E2E enabled)")
    if not settings.credentials.api_key_user:
        pytest.fail("BAND_API_KEY_USER not set (E2E enabled)")
    for dep in marker.args[0]:
        require_dep(dep, settings)


@pytest.fixture(scope="session")
def baseline_settings() -> BaselineSettings:
    return BaselineSettings()


@pytest.fixture(scope="session")
def baseline_run_id() -> str:
    """Token identifying this session's minted resources (for naming/sweep)."""
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
def user_ops(baseline_settings: BaselineSettings) -> UserOps:
    """User-operation driver, authenticated as the test user.

    BAND_API_KEY_USER is a required prerequisite: fail loudly rather than skip.
    requires_e2e already gates that E2E is enabled, so a missing key here is a
    real misconfiguration, not a reason to silently skip.
    """
    assert baseline_settings.credentials.api_key_user, (
        "BAND_API_KEY_USER is required for the user-operations driver"
    )
    client = AsyncRestClient(
        api_key=baseline_settings.credentials.api_key_user,
        base_url=baseline_settings.endpoints.rest_url,
    )
    return UserOps(client)


@pytest.fixture
def judge(
    baseline_settings: BaselineSettings,
) -> Callable[..., Awaitable[Verdict]]:
    """LLM judge with model + api_key pre-bound; call with criteria/transcript.

    Self-gates on its provider key so any test using it skips cleanly when the
    key is absent — the requirement travels with the fixture.

    Usage::

        verdict = await judge(criteria="...", transcript="...")
    """
    require_dep(Dep.ANTHROPIC, baseline_settings)
    return functools.partial(
        _judge,
        model=baseline_settings.llm_models.judge_model,
        api_key=baseline_settings.llm_credentials.anthropic_api_key,
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
def langgraph_adapter(baseline_settings: BaselineSettings):
    # TODO turn adapters creation into a factory or some other generic no glue code mechanism
    """A cheap LangGraph agent built from settings (OpenAI-backed)."""
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


@pytest.fixture
def anthropic_adapter(baseline_settings: BaselineSettings):
    # TODO turn adapters creation into a factory or some other generic no glue code mechanism
    """A cheap Anthropic agent built from settings."""
    from band.adapters.anthropic import AnthropicAdapter

    return AnthropicAdapter(
        model=baseline_settings.llm_models.anthropic_model,
        provider_key=baseline_settings.llm_credentials.anthropic_api_key,
        prompt=_SHORT_PROMPT,
    )


@pytest.fixture
def reply_capture(
    baseline_ws: TrackingWebSocketClient,
) -> Callable[[str], AbstractAsyncContextManager[ReplyCapture]]:
    """Subscribe-before-send capture with the WS observer pre-bound.

    Hides ``baseline_ws`` from tests; use as ``async with reply_capture(room_id)``.
    """
    return functools.partial(_reply_capture, baseline_ws)


@pytest.fixture
async def resource_manager(
    baseline_settings: BaselineSettings,
    baseline_user_client: AsyncRestClient,
    baseline_run_id: str,
) -> AsyncGenerator[ResourceManager, None]:
    """Per-test mint/reap driver.

    Teardown force-deletes everything minted this run, unless
    ``BAND_E2E_AUTOCLEAN`` is false (kept for on-purpose debugging; surviving
    ids are logged by ``reap_all``/``mint_*``).
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
