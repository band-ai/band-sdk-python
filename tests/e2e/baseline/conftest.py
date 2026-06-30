"""Pytest wiring for the baseline toolkit.

This file holds only the pytest *glue*: the marker registration, the always-on
E2E/Band-key gate, and the CI-lane collection hook. The fixtures themselves live
in ``fixtures/`` (platform / agents / capture) and the lane-selection logic in
``lane_selection``; both are imported here so pytest registers the fixtures for
the baseline subtree (``pytest_plugins`` is deprecated in a non-root conftest).
The fixture re-exports are listed in ``__all__`` so they read as intentional.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.fixtures.agents import (
    adapter_id,
    agent,
    agents,
    matrix_agent,
)
from tests.e2e.baseline.fixtures.capture import judge, reply_capture
from tests.e2e.baseline.fixtures.platform import (
    baseline_run_id,
    baseline_settings,
    baseline_user_client,
    baseline_ws,
    orphan_sweep,
    resource_manager,
    user_ops,
)
from tests.e2e.baseline.agents import AGENTS_MARKER, MATRIX_MARKER
from tests.e2e.baseline.lane_selection import (
    MIXED_LANE_MARKER,
    apply_lane_skips,
    assert_no_unschedulable_mixed_lane,
)
from tests.e2e.baseline.requires import MARKER, require_dep
from tests.e2e.baseline.settings import BaselineSettings

# Re-exported fixtures (defined in fixtures/*; imported so pytest registers them).
__all__ = [
    "adapter_id",
    "agent",
    "agents",
    "baseline_run_id",
    "baseline_settings",
    "baseline_user_client",
    "baseline_ws",
    "judge",
    "matrix_agent",
    "orphan_sweep",
    "reply_capture",
    "resource_manager",
    "user_ops",
]


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
    config.addinivalue_line(
        "markers",
        f"{MIXED_LANE_MARKER}: opt a @with_agents test out of the mixed-lane guard; "
        "declares it deliberately local-only (unschedulable under CI lane scoping).",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Gate every baseline test, then resolve any ``@requires(...)`` extras.

    The gate is unconditional (all baseline tests are live-e2e): E2E disabled
    -> skip; E2E enabled but BAND_API_KEY_USER missing -> fail (misconfig). The
    toolkit drives the platform as the user and provisions its own agents (with
    per-agent generated keys), so a pre-existing static BAND_API_KEY is not
    required. A test only needs ``@requires(...)`` to declare *additional*
    optional capabilities (e.g. provider keys), which fail with the requirement
    reason when absent.
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


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Guard unschedulable mixed-lane tests, then scope to ``BAND_E2E_LANE``.

    The guard runs in every collection (lane-scoped or not) so a structurally
    unschedulable ``@with_agents`` test fails before it ever reaches CI; lane
    scoping then applies the ``BAND_E2E_LANE`` skips (see ``lane_selection``).
    """
    assert_no_unschedulable_mixed_lane(items)
    apply_lane_skips(config, items)
