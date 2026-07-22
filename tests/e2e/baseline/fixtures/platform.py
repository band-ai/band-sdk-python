"""Platform/session fixtures: settings, the user REST client, WS observer, and
provision/reap lifecycle (including the session-start orphan sweep and the
per-test reaper for agents a killed test abandons)."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

import pytest
from band_rest import AsyncRestClient

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    new_run_id,
    user_rest_client,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient, user_ws_observer

logger = logging.getLogger(__name__)

__all__ = [
    "baseline_run_id",
    "baseline_settings",
    "baseline_user_client",
    "baseline_ws",
    "orphan_sweep",
    "reap_leaked_agents",
    "resource_manager",
    "user_ops",
]


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
    return user_rest_client(baseline_settings)


@pytest.fixture
def user_ops(baseline_user_client: AsyncRestClient) -> UserOps:
    """User-operation driver over the session-scoped user REST client.

    Reuses ``baseline_user_client`` (which already requires BAND_API_KEY_USER)
    rather than spinning up a fresh client per test.
    """
    return UserOps(baseline_user_client)


@pytest.fixture(scope="session")
async def baseline_ws(
    baseline_settings: BaselineSettings,
) -> AsyncGenerator[TrackingWebSocketClient, None]:
    """User-authenticated WS observer for the wait primitives.

    Session-scoped to avoid per-test connect/teardown latency; channels are
    left on teardown. Construction lives in ``user_ws_observer`` (shared with
    pytest-free callers).
    """
    async with user_ws_observer(baseline_settings) as tracking:
        yield tracking


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


@pytest.fixture(autouse=True)
async def reap_leaked_agents() -> AsyncGenerator[None, None]:
    """Stop agents (and free their locks) abandoned by a killed test.

    pytest-timeout's signal method aborts a test without unwinding its
    ``async with agent`` blocks: the agent's tasks stay alive on the
    session loop (a zombie that keeps consuming room messages) and its
    single-instance lock stays held, refusing every rerun and later test
    using the same agent id. Stopping the agent — not merely releasing
    its lock — removes the zombie so the next start is a true singleton.
    Reruns re-run function fixtures, so reaping here heals them too.
    """
    from band import agent as agent_module
    from band.runtime import single_instance

    yield
    for leaked_agent in agent_module.running_agents():
        logger.warning(
            "Stopping agent leaked by a killed test: %s",
            leaked_agent.runtime.agent_id,
        )
        try:
            await leaked_agent.stop(timeout=None)
        except Exception:
            # A zombie's transport may already be dead; the lock backstop
            # below must still run for the remaining leaks.
            logger.exception("Leaked agent %s did not stop cleanly", leaked_agent)
    # Backstop for guards whose owner is unreachable (e.g. a runtime
    # driven without Agent): a held lock with no live owner can only
    # block, never protect.
    leaked_locks = single_instance.release_all_held()
    if leaked_locks:
        logger.warning(
            "Released single-instance lock(s) abandoned by a killed test: %s",
            ", ".join(leaked_locks),
        )
