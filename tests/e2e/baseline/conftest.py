"""Fixtures for the baseline testing toolkit.

Config comes from the concern-separated ``BaselineSettings`` (see settings.py),
not the legacy flat ``E2ESettings``. Provisioning (mint/reap) and the other
tools add their fixtures here as they are built.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from band_rest import AsyncRestClient

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.tools.provisioning import ResourceManager, new_run_id
from tests.e2e.baseline.tools.user_ops import UserOps


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
