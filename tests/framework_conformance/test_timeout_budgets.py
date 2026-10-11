"""Phase deadlines must expire before the whole-test watchdog on every host."""

from __future__ import annotations

import pytest

from tests.e2e.baseline.timeouts import busy_room_budget, slow_turn_budget
from tests.paths import REPO_ROOT
from tests.toolkit.timeouts import TIMEOUT_BACKSTOP_MARGIN_S, backstop_timeout


@pytest.mark.parametrize("turn_budget_s", [30, 120, 240])
def test_busy_room_backstop_covers_all_unchanged_phase_deadlines(
    turn_budget_s: int,
) -> None:
    budget = busy_room_budget(turn_budget_s)
    assert (budget.seed_s, budget.flood_s, budget.recall_s) == (
        turn_budget_s,
        4 * turn_budget_s,
        turn_budget_s,
    )
    hard = backstop_timeout(turn_budget_s, extra_s=budget.extra_s)
    assert (
        hard - (budget.seed_s + budget.flood_s + budget.recall_s)
        == TIMEOUT_BACKSTOP_MARGIN_S
    )


@pytest.mark.parametrize("turn_budget_s", [30, 120, 240])
@pytest.mark.parametrize("barriers", [1, 2, 3])
def test_slow_turn_backstop_covers_every_barrier(
    turn_budget_s: int, barriers: int
) -> None:
    budget = slow_turn_budget(turn_budget_s, barriers=barriers)
    hard = backstop_timeout(turn_budget_s, extra_s=budget.extra_s)
    assert hard - budget.deadline_s * barriers >= TIMEOUT_BACKSTOP_MARGIN_S


@pytest.mark.parametrize("turn_budget_s", [30, 120, 240])
def test_collected_busy_room_backstop_covers_its_phases(
    turn_budget_s: int,
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E2E_TIMEOUT", str(turn_budget_s))
    pytester.makeconftest(f"""
from __future__ import annotations
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
from tests.e2e.baseline.conftest import pytest_collection_modifyitems

def pytest_collection_finish(session):
    assert len(session.items) == 1
    marker = session.items[0].get_closest_marker("timeout")
    assert marker.args == ({6 * turn_budget_s + TIMEOUT_BACKSTOP_MARGIN_S},)
""")
    scenario = REPO_ROOT / "tests/e2e/baseline/smoke/matrix/test_noisy_room.py"
    result = pytester.runpytest_subprocess(
        f"{scenario}::test_recall_and_ignore_crosstalk_in_busy_room[pydantic_ai]",
        "--collect-only",
        "--no-cov",
        "-p",
        "no:rerunfailures",
        timeout=30,
    )
    assert result.ret == pytest.ExitCode.OK
