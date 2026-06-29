"""Platform/session fixtures: settings, the user REST client, WS observer, and
provision/reap lifecycle (including the session-start orphan sweep)."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from band_rest import AsyncRestClient

from band.client.streaming import WebSocketClient

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    new_run_id,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.helpers import TrackingWebSocketClient

__all__ = [
    "baseline_run_id",
    "baseline_settings",
    "baseline_user_client",
    "baseline_ws",
    "orphan_sweep",
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
