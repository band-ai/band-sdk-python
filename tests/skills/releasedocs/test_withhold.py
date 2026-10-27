"""The docs review never sees a PR the ledger cites or a held item comes from."""

from __future__ import annotations

import json
import logging
from types import ModuleType

import pytest

from tests.skills.releasedocs.holdbackfiles import Inputs


def argv(inputs: Inputs) -> list[str]:
    """The review range is the release's own PRs here."""
    return [str(inputs.prs), str(inputs.notes), "--ledger", str(inputs.ledger)]


def review_numbers(output: str) -> list[int]:
    return [pr["number"] for pr in json.loads(output)["pull_requests"]]


class TestWithhold:
    @pytest.mark.parametrize(
        ("held", "kept"),
        [([], [101, 102]), (["2"], [101])],
        ids=["ledger-cited", "ledger-cited-and-review-held"],
    )
    def test_held_prs_are_dropped_from_the_review(
        self,
        withhold: ModuleType,
        inputs: Inputs,
        capsys: pytest.CaptureFixture[str],
        held: list[str],
        kept: list[int],
    ) -> None:
        assert withhold.main([*argv(inputs), "--held", *held]) == 0
        assert review_numbers(capsys.readouterr().out) == kept

    def test_unreadable_ledger_fails_closed(
        self, withhold: ModuleType, inputs: Inputs, capsys: pytest.CaptureFixture[str]
    ) -> None:
        inputs.ledger.write_text("held_items: [", encoding="utf-8")
        assert withhold.main(argv(inputs)) == 1
        assert capsys.readouterr().out == ""

    def test_log_carries_counts_and_no_ledger_text(
        self,
        withhold: ModuleType,
        inputs: Inputs,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO)
        assert withhold.main(argv(inputs)) == 0
        assert "Withheld 1 of 3 review source(s)" in caplog.text
        assert "Not public yet." not in caplog.text
        assert "sdk-901" not in caplog.text
