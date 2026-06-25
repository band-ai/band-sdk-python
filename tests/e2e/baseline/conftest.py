"""Fixtures for the baseline testing toolkit.

Config comes from the concern-separated ``BaselineSettings`` (see settings.py),
not the legacy flat ``E2ESettings``. Provisioning (mint/reap) and the other
tools add their fixtures here as they are built.
"""

from __future__ import annotations

import pytest
from band_rest import AsyncRestClient

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.tools.user_ops import UserOps


@pytest.fixture(scope="session")
def baseline_settings() -> BaselineSettings:
    return BaselineSettings()


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
