from __future__ import annotations

import pytest

from tests.e2e.baseline.smoke.samples.approvals import (
    UNATTENDED_POLICIES,
    UnattendedPolicy,
    assert_shell_write_attempted,
)

SHELL_POLICIES = tuple(
    policy
    for policy in UNATTENDED_POLICIES
    if policy.assert_attempted is assert_shell_write_attempted
)


@pytest.fixture(params=SHELL_POLICIES, ids=lambda policy: policy.name)
def shell_policy(request: pytest.FixtureRequest) -> UnattendedPolicy:
    return request.param
