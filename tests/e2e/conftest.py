"""E2E test collection hook and fixture registration.

E2E tests run adapters against a real Band platform with real (cheap) LLMs.
They verify platform functionality and integration correctness, not LLM output
quality.

Run manually only, never in CI/CD:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/ -v -s --no-cov

Shared settings, skip markers, and types live in ``tests.e2e.settings`` (a plain
module) so fixtures, helpers, and tests import them without importing from a
conftest. Fixtures live in concern-focused modules — ``fixtures.clients`` (config
+ REST/WS clients), ``fixtures.rooms`` (room allocation + agent identity),
``fixtures.memory`` (memory toolkit) — and are imported into this conftest's
namespace below so they stay scoped to ``tests/e2e/`` (``pytest_plugins`` is only
honored in the top-level conftest).
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Registering fixtures: pytest discovers fixtures imported into a conftest's
# namespace. The fixture modules import only from ``tests.e2e.settings`` (never
# this conftest), so these imports are free of circular dependencies.
from tests.e2e.fixtures.clients import (  # noqa: F401
    api_client,
    e2e_config,
    e2e_created_room_ids,
    e2e_room_summary,
    e2e_session_client,
    e2e_session_client_2,
    e2e_user_client,
    ws_client,
)
from tests.e2e.fixtures.memory import memory  # noqa: F401
from tests.e2e.fixtures.rooms import (  # noqa: F401
    adapter_entry,
    e2e_adapter_room,
    e2e_agent_id,
    e2e_agent_info,
    e2e_agent_info_2,
    e2e_fresh_room_allocator,
    e2e_isolation_room_b,
    e2e_parlant_room,
    e2e_room_allocator,
)
from tests.e2e.settings import E2ESettings


def _effective_timeout(item: pytest.Item, base: int) -> int | None:
    """Resolve an e2e item's pytest-timeout from its (optional) ``timeout`` marker.

    The ``timeout`` marker is pytest-timeout's own, overloaded here so a test reads
    in pytest-native spelling:

    * no marker, or bare ``@pytest.mark.timeout()`` -> ``base`` (the turn budget);
    * ``@pytest.mark.timeout(extra=n)`` -> ``base + n`` (headroom for a slow
      framework, e.g. crewai cold-start), tracking ``base`` rather than hardcoding;
    * ``@pytest.mark.timeout(n)`` (positional) -> ``None``, i.e. leave it alone so
      pytest-timeout honors the absolute value natively (the long scenario tests).

    Returning a value means the caller adds a clean ``timeout(value)`` marker; the
    ``extra=``/bare forms are illegal to pytest-timeout's own parser, so the caller
    must make that clean marker the *closest* one (it is the only one parsed).
    """
    marker = item.get_closest_marker("timeout")
    if marker is None:
        return base
    if marker.args:  # absolute @pytest.mark.timeout(n): honored natively
        return None
    return base + marker.kwargs.get("extra", 0)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply E2E-specific markers to all collected tests in this directory.

    1. ``asyncio(loop_scope="session")`` — Fixtures default to the session
       loop via ``asyncio_default_fixture_loop_scope`` in pyproject.toml,
       but test functions default to function-scoped loops. This mismatch
       causes "Future attached to a different loop" errors when tests call
       into session-scoped WS/REST clients.

    2. ``timeout`` — E2E tests interact with live platforms and LLMs, so they need
       more time than the 30s default in pyproject.toml. Every test gets the
       configured turn budget (``E2E_TIMEOUT``, via ``E2ESettings``) unless it opts
       into more with ``@pytest.mark.timeout(extra=n)`` or sets an absolute
       ``@pytest.mark.timeout(n)``; see :func:`_effective_timeout`. ``pytestmark``
       in conftest.py is NOT applied to collected tests, so the marker is added
       here. It is prepended (``append=False``) so this clean ``timeout(n)`` is the
       marker pytest-timeout reads — ahead of any ``extra=``/bare form on the test,
       which pytest-timeout's parser would otherwise reject.
    """
    e2e_dir = Path(__file__).parent
    session_marker = pytest.mark.asyncio(loop_scope="session")
    base = E2ESettings().e2e_timeout
    for item in items:
        if not Path(item.path).is_relative_to(e2e_dir):
            continue
        item.add_marker(session_marker)
        timeout = _effective_timeout(item, base)
        if timeout is not None:
            item.add_marker(pytest.mark.timeout(timeout), append=False)
