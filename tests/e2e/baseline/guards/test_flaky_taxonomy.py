"""Guard for the baseline flakiness taxonomy (``tests/e2e/baseline/flaky.py``).

Pins the two things that would silently corrupt the suite's reliability if they
regressed: that ``flaky_infra`` keeps ``rerun_except=["AssertionError"]`` (so a real
assertion bug fails loud instead of being retried away) while ``flaky_model`` does
not, and that the collection guard rejects a raw ``@pytest.mark.flaky`` (which would
let an unclassified rerun policy slip in).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

import pytest

from tests.e2e.baseline.flaky import (
    assert_flaky_is_classified,
    flaky_infra,
    flaky_model,
)


def _marks(func: Callable[..., Any]) -> dict[str, Any]:
    """The pytest marks a decorator stamped on ``func``, keyed by marker name."""
    return {m.name: m for m in getattr(func, "pytestmark", [])}


def test_flaky_model_retries_assertion_errors_and_stamps_its_kind() -> None:
    @flaky_model("recall is model-dependent")
    def t() -> None: ...

    marks = _marks(t)
    # AssertionError IS retried (a capable model occasionally misses) -> no exclusion.
    assert "rerun_except" not in marks["flaky"].kwargs
    assert marks["flaky_reason"].kwargs == {
        "kind": "model",
        "reason": "recall is model-dependent",
    }


def test_flaky_infra_keeps_assertion_errors_failing_loud() -> None:
    @flaky_infra("a live-turn timeout is transient")
    def t() -> None: ...

    marks = _marks(t)
    # The whole point: only transient (non-assertion) errors retry; a real assertion
    # failure is NOT retried away.
    assert marks["flaky"].kwargs["rerun_except"] == ["AssertionError"]
    assert marks["flaky_reason"].kwargs["kind"] == "infra"


def _item(*marker_names: str) -> SimpleNamespace:
    marks = {name: SimpleNamespace(name=name) for name in marker_names}
    return SimpleNamespace(nodeid="pkg/test_x.py::t", get_closest_marker=marks.get)


def test_guard_rejects_a_raw_flaky_marker() -> None:
    with pytest.raises(ValueError, match="raw @pytest.mark.flaky"):
        assert_flaky_is_classified([_item("flaky")])


def test_guard_accepts_a_taxonomy_stamped_flaky() -> None:
    # A flaky marker carrying the taxonomy stamp is fine (went through the decorators).
    assert_flaky_is_classified([_item("flaky", "flaky_reason")])
