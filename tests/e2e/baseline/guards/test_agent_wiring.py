"""Guard tests for agent-fixture wiring (``agent_wiring.assert_agent_fixtures_wired``).

Two layers:

* **Synthetic items** exercise every wiring rule fast, through the public guard.
* **Real ``pytester`` collections** cover what a synthetic item can't: the actual
  indirect-parametrize / fixture-closure interaction that ``@per_adapter``'s
  ``usefixtures("adapter_id")`` exists to satisfy — an ``agent``-only test must *collect*,
  not error with "function uses no fixture 'adapter_id'".
"""

from __future__ import annotations

import textwrap
from types import SimpleNamespace

import pytest

from tests.e2e.baseline.agent_wiring import assert_agent_fixtures_wired
from tests.e2e.baseline.agents import WITH_ADAPTERS_MARKER, PER_ADAPTER_MARKER


class _FakeItem:
    """Minimal ``pytest.Item`` stand-in: only what ``_wiring_error`` reads."""

    def __init__(
        self,
        nodeid: str = "t.py::test",
        *,
        each: bool = False,
        group: bool = False,
        fixturenames: tuple[str, ...] = (),
    ) -> None:
        self.nodeid = nodeid
        self.fixturenames = fixturenames
        self._markers: dict[str, SimpleNamespace] = {}
        if each:
            self._markers[PER_ADAPTER_MARKER] = SimpleNamespace(
                args=(SimpleNamespace(),)
            )
        if group:
            self._markers[WITH_ADAPTERS_MARKER] = SimpleNamespace(
                args=(SimpleNamespace(),)
            )

    def get_closest_marker(self, name: str) -> SimpleNamespace | None:
        return self._markers.get(name)


def _reason(item: _FakeItem) -> str:
    """The guard's error message for a single offending item."""
    with pytest.raises(pytest.UsageError) as excinfo:
        assert_agent_fixtures_wired([item])
    return str(excinfo.value)


# --- valid wirings collect silently -----------------------------------------------------


@pytest.mark.parametrize(
    "item",
    [
        _FakeItem(each=True, fixturenames=("agent", "adapter_id")),
        _FakeItem(each=True, fixturenames=("cell", "adapter_id")),
        _FakeItem(group=True, fixturenames=("agent",)),
        _FakeItem(group=True, fixturenames=("agents",)),
        _FakeItem(fixturenames=("resource_manager",)),  # adapter-agnostic test
    ],
    ids=["each+agent", "each+cell", "group+agent", "group+agents", "agnostic"],
)
def test_valid_wiring_is_allowed(item: _FakeItem) -> None:
    assert_agent_fixtures_wired([item])  # no raise


# --- each rule is enforced ---------------------------------------------------------------


def test_cell_requires_per_adapter() -> None:
    assert "without @per_adapter" in _reason(_FakeItem(fixturenames=("cell",)))


def test_agent_and_cell_are_mutually_exclusive() -> None:
    assert "both `agent` and `cell`" in _reason(
        _FakeItem(each=True, fixturenames=("agent", "cell"))
    )


def test_cell_is_fan_only() -> None:
    assert "fan-only" in _reason(_FakeItem(group=True, fixturenames=("cell",)))


def test_agents_is_group_only() -> None:
    assert "a cell is one adapter" in _reason(
        _FakeItem(each=True, fixturenames=("agents",))
    )


def test_one_topology_per_test() -> None:
    assert "one topology" in _reason(
        _FakeItem(each=True, group=True, fixturenames=("agent",))
    )


def test_agent_requires_a_decorator() -> None:
    assert "no @per_adapter" in _reason(_FakeItem(fixturenames=("agent",)))


def test_no_hand_rolled_matrix() -> None:
    # `adapter_id` requested (or hand-parametrized) without @per_adapter — caught via the
    # fixture closure, so a plain `def test(adapter_id)` is flagged at collection too.
    assert "hand-rolled matrix" in _reason(_FakeItem(fixturenames=("adapter_id",)))


def test_decorator_that_provisions_nothing_is_rejected() -> None:
    # @per_adapter / @with_adapters that requests no agent/agents/cell would run nothing.
    assert "requests none of agent/agents/cell" in _reason(
        _FakeItem(each=True, fixturenames=("adapter_id",))
    )
    assert "requests none of agent/agents/cell" in _reason(
        _FakeItem(group=True, fixturenames=())
    )


def test_all_offenders_are_reported() -> None:
    offenders = [
        _FakeItem("t.py::a", fixturenames=("cell",)),
        _FakeItem("t.py::b", fixturenames=("agent",)),
    ]
    with pytest.raises(pytest.UsageError) as excinfo:
        assert_agent_fixtures_wired(offenders)
    message = str(excinfo.value)
    assert "t.py::a" in message and "t.py::b" in message


# --- real collection via pytester --------------------------------------------------------

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
