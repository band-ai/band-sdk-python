"""Pytest wiring for the baseline toolkit.

This file holds only the pytest *glue*: the marker registration, the always-on
E2E/Band-key gate, and the CI-lane collection hook. The fixtures themselves live
in ``fixtures/`` (platform / agents / capture) and the lane-selection logic in
``lane_selection``; both are imported here so pytest registers the fixtures for
the baseline subtree (``pytest_plugins`` is deprecated in a non-root conftest).
The fixture re-exports are listed in ``__all__`` so they read as intentional.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from tests.e2e.baseline.fixtures.agents import (
    adapter_id,
    agent,
    agents,
    cell,
    peer,
)
from tests.e2e.baseline.fixtures.capture import judge, reply_capture
from tests.e2e.baseline.fixtures.platform import (
    baseline_run_id,
    baseline_settings,
    baseline_user_client,
    baseline_ws,
    orphan_sweep,
    reap_leaked_agents,
    resource_manager,
    user_ops,
)
from tests.e2e.baseline.agents import (
    LANE_MARKER,
    WITH_ADAPTERS_MARKER,
    PER_ADAPTER_MARKER,
)
from tests.e2e.baseline.agent_wiring import assert_agent_fixtures_wired
from tests.e2e.baseline.flaky import assert_flaky_is_classified
from tests.e2e.baseline.lane_selection import (
    apply_lane_skips,
    assert_every_item_is_schedulable,
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
    "cell",
    "judge",
    "orphan_sweep",
    "peer",
    "reap_leaked_agents",
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
        f"{WITH_ADAPTERS_MARKER}(request): set by @with_adapters to declare the adapters a "
        "test runs; resolved by the agent/agents fixtures. See agents.py.",
    )
    config.addinivalue_line(
        "markers",
        f"{PER_ADAPTER_MARKER}(build): set by @per_adapter to steer per-cell construction "
        "(prompt/features/tools/peer); resolved by the cell/agent/peer fixtures.",
    )
    config.addinivalue_line(
        "markers",
        f"{LANE_MARKER}(lane): set by @lane(Lane.X) to assign a cross-lane test to one "
        "explicit CI lane, overriding derived home-lane scheduling. See lane_selection.",
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


def _effective_timeout(item: pytest.Item, settings: BaselineSettings) -> int | None:
    marker = item.get_closest_marker("timeout")
    if marker is None:
        return settings.e2e_default_backstop_timeout()
    if marker.args:
        return None
    return settings.e2e_default_backstop_timeout(extra=marker.kwargs.get("extra", 0))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Guard wiring + schedulability, scope to ``BAND_E2E_LANE``, then apply the
    session event loop + per-turn timeout to every baseline test.

    Both guards run in every collection (lane-scoped or not) so a mis-wired or
    unschedulable test fails before it ever reaches CI; lane scoping then applies the
    ``BAND_E2E_LANE`` skips (see ``lane_selection``). Order is load-bearing: guard
    before skip, so an unschedulable test is a loud error rather than a silent skip.

    The final loop applies the markers baseline tests need (baseline is a plain
    module tree with no auto-marking otherwise):

    * ``asyncio(loop_scope="session")`` — align async tests with the
      session-scoped fixtures (WS/REST clients), which
      ``asyncio_default_fixture_loop_scope`` puts on the session loop; a
      function-scoped test loop would raise "attached to a different loop".
    * ``timeout`` — live turns need far more than the 30s pyproject default; the
      settings backstop gives each test the ``E2E_TIMEOUT`` budget (plus any
      ``extra=``), prepended (``append=False``) so this clean ``timeout(n)`` is
      the marker pytest-timeout reads, ahead of any ``extra=``/bare form it
      would reject.
    """
    assert_agent_fixtures_wired(items)
    assert_every_item_is_schedulable(items)

    baseline_dir = Path(__file__).parent
    baseline_items = [
        item for item in items if Path(item.path).is_relative_to(baseline_dir)
    ]
    assert_flaky_is_classified(baseline_items)

    settings = BaselineSettings()
    apply_lane_skips(settings.run.lane, items)

    session_marker = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if not Path(item.path).is_relative_to(baseline_dir):
            continue
        if inspect.iscoroutinefunction(getattr(item, "obj", None)):
            item.add_marker(session_marker)
        timeout = _effective_timeout(item, settings)
        if timeout is not None:
            item.add_marker(pytest.mark.timeout(timeout), append=False)
