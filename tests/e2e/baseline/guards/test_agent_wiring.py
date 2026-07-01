"""Real-collection (fixture-closure) tests for agent-fixture wiring.

The fast synthetic-item tests of the pure guard policy (``_wiring_error`` via
``assert_agent_fixtures_wired``) live in
``tests/framework_conformance/test_agent_wiring_rules.py`` so they run in every PR. This
file keeps only what a synthetic item can't cover: the actual indirect-parametrize /
fixture-closure interaction that ``@per_adapter``'s ``usefixtures("adapter_id")`` exists
to satisfy — an ``agent``-only test must *collect*, not error with "function uses no
fixture 'adapter_id'". These drive a real ``pytester`` sub-collection, so they need the
real fixtures.
"""

from __future__ import annotations

import textwrap

import pytest


# A minimal conftest for the sub-run: the real fixtures + guard, with stubbed platform
# fixtures (collection never runs fixture bodies, so stubs suffice to resolve the closure).
_SUBRUN_CONFTEST = """
import pytest
from tests.e2e.baseline.fixtures.agents import adapter_id, agent, agents, cell  # noqa: F401
from tests.e2e.baseline.agents import WITH_ADAPTERS_MARKER, PER_ADAPTER_MARKER
from tests.e2e.baseline.agent_wiring import assert_agent_fixtures_wired

@pytest.fixture
def baseline_settings():
    return None

@pytest.fixture
def resource_manager():
    return None

def pytest_configure(config):
    config.addinivalue_line("markers", f"{PER_ADAPTER_MARKER}(build): per_adapter steering")
    config.addinivalue_line("markers", f"{WITH_ADAPTERS_MARKER}(request): with_adapters request")

def pytest_collection_modifyitems(items):
    assert_agent_fixtures_wired(items)
"""


def _collect(pytester: pytest.Pytester, body: str) -> pytest.RunResult:
    pytester.makeconftest(_SUBRUN_CONFTEST)
    pytester.makepyfile(textwrap.dedent(body))
    # asyncio_mode=auto mirrors the repo (the tmpdir has no pyproject to inherit it from).
    return pytester.runpytest("--collect-only", "-o", "asyncio_mode=auto")


def test_per_adapter_agent_collects(pytester: pytest.Pytester) -> None:
    """The regression a synthetic item can't catch: usefixtures keeps this collectable."""
    result = _collect(
        pytester,
        """
        from tests.e2e.baseline.agents import per_adapter

        @per_adapter()
        def test_live(agent):
            pass
        """,
    )
    assert result.ret == 0
    result.stdout.fnmatch_lines(["*test_live*"])


def test_per_adapter_cell_collects(pytester: pytest.Pytester) -> None:
    result = _collect(
        pytester,
        """
        from tests.e2e.baseline.agents import per_adapter

        @per_adapter()
        def test_build(cell):
            pass
        """,
    )
    assert result.ret == 0


def test_agent_and_cell_together_fails_collection(pytester: pytest.Pytester) -> None:
    result = _collect(
        pytester,
        """
        from tests.e2e.baseline.agents import per_adapter

        @per_adapter()
        def test_bad(agent, cell):
            pass
        """,
    )
    assert result.ret != 0
    assert "both `agent` and `cell`" in (result.stdout.str() + result.stderr.str())


def test_bare_agent_fails_collection(pytester: pytest.Pytester) -> None:
    result = _collect(
        pytester,
        """
        def test_bare(agent):
            pass
        """,
    )
    assert result.ret != 0
    assert "no @per_adapter" in (result.stdout.str() + result.stderr.str())


def test_empty_selection_fails_at_import(pytester: pytest.Pytester) -> None:
    """A contradictory filter (supports ∧ without the same capability) selects nothing."""
    result = _collect(
        pytester,
        """
        from band.core.types import Capability
        from tests.e2e.baseline.agents import per_adapter

        @per_adapter(supports={Capability.MEMORY}, without={Capability.MEMORY})
        def test_never(agent):
            pass
        """,
    )
    assert result.ret != 0
    assert "selected no adapters" in (result.stdout.str() + result.stderr.str())
