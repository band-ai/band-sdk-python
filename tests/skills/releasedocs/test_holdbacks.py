"""Holdbacks remove ledger-held and review-flagged items, and fail closed on doubt."""

from __future__ import annotations

import logging
from collections.abc import Callable
from types import ModuleType

import pytest

from tests.skills.releasedocs.holdbackfiles import Inputs, release_prs
from tests.skills.releasedocs.samples import (
    HELD_PR,
    HELD_SOURCE,
    LEDGER,
    LEDGER_ID,
    LEDGER_REASON,
    PLATFORM_SOURCE,
    RELEASED_LEDGER,
)


class TestHoldbacks:
    def test_ledger_match_holds_the_item_the_review_missed(
        self, holdbacks: ModuleType, inputs: Inputs
    ) -> None:
        assert holdbacks.main(inputs.argv()) == 0
        assert inputs.outputs_written() == {"has_entry": "true", "held": "1"}
        assert inputs.kept_labels() == ["Send Retries", "Token Redaction"]

    def test_review_flag_holds_an_item_the_ledger_does_not_cite(
        self, holdbacks: ModuleType, inputs: Inputs
    ) -> None:
        inputs.write(release_prs(), LEDGER, {"held": [2], "hold_breaking": False})
        assert holdbacks.main(inputs.argv()) == 0
        assert inputs.outputs_written() == {"has_entry": "true", "held": "1 2"}
        assert inputs.kept_labels() == ["Send Retries"]

    @pytest.mark.parametrize(
        "ledger",
        [
            RELEASED_LEDGER,
            LEDGER.replace("surfaces: [sdks, platform]", "surfaces: [platform]"),
            LEDGER.replace(HELD_SOURCE, f"band-sdk-typescript#{HELD_PR}"),
            RELEASED_LEDGER.replace(PLATFORM_SOURCE, "https://example.invalid/pull/77"),
        ],
        ids=[
            "already-released",
            "other-surface",
            "other-repo",
            "unparsed-source-of-another-surface",
        ],
    )
    def test_entry_not_held_from_this_sdk_holds_nothing(
        self, holdbacks: ModuleType, inputs: Inputs, ledger: str
    ) -> None:
        inputs.write(release_prs(), ledger, {"held": [], "hold_breaking": False})
        assert holdbacks.main(inputs.argv()) == 0
        assert inputs.outputs_written() == {"has_entry": "true", "held": ""}

    @pytest.mark.parametrize(
        "source",
        [
            lambda _: f"band-ai/{HELD_SOURCE}",
            lambda _: HELD_SOURCE.upper(),
            lambda releasedata: f"thenvoi/{releasedata.FORMER_SDK_REPO_NAME}#{HELD_PR}",
        ],
        ids=["owner-qualified", "other-case", "former-repo-name"],
    )
    def test_every_spelling_of_the_sdk_repo_holds_the_item(
        self,
        holdbacks: ModuleType,
        releasedata: ModuleType,
        inputs: Inputs,
        source: Callable[[ModuleType], str],
    ) -> None:
        ledger = LEDGER.replace(HELD_SOURCE, source(releasedata))
        inputs.write(release_prs(), ledger, {"held": [], "hold_breaking": False})
        assert holdbacks.main(inputs.argv()) == 0
        assert inputs.outputs_written() == {"has_entry": "true", "held": "1"}

    def test_every_item_held_leaves_no_entry(
        self, holdbacks: ModuleType, inputs: Inputs
    ) -> None:
        inputs.write(release_prs(), LEDGER, {"held": [0, 2], "hold_breaking": False})
        assert holdbacks.main(inputs.argv()) == 0
        assert inputs.outputs_written() == {"has_entry": "false", "held": "0 1 2"}
        assert not inputs.out.exists()

    @pytest.mark.parametrize(
        ("prs", "ledger", "report"),
        [
            (
                release_prs(breaking=(HELD_PR,)),
                LEDGER,
                {"held": [], "hold_breaking": False},
            ),
            (release_prs(), LEDGER, {"held": [], "hold_breaking": True}),
            (release_prs(), LEDGER, {"held": [3], "hold_breaking": False}),
            (release_prs(), LEDGER, {"held": []}),
            (
                release_prs(),
                "held_items: [{id: x}]",
                {"held": [], "hold_breaking": False},
            ),
            (release_prs(), "held_items: [", {"held": [], "hold_breaking": False}),
            (
                release_prs(),
                LEDGER.replace(
                    HELD_SOURCE,
                    f"https://github.com/band-ai/band-sdk-python/pull/{HELD_PR}",
                ),
                {"held": [], "hold_breaking": False},
            ),
        ],
        ids=[
            "ledger-holds-the-breaking-change",
            "review-holds-the-breaking-change",
            "unknown-index",
            "invalid-report",
            "invalid-ledger",
            "unreadable-ledger",
            "unparsed-sdks-source",
        ],
    )
    def test_undecidable_holdbacks_fail_closed(
        self,
        holdbacks: ModuleType,
        inputs: Inputs,
        prs: dict[str, object],
        ledger: str,
        report: dict[str, object],
    ) -> None:
        inputs.write(prs, ledger, report)
        assert holdbacks.main(inputs.argv()) == 1
        assert (inputs.outputs_written(), inputs.out.exists()) == ({}, False)

    @pytest.mark.parametrize("missing", ["report", "ledger"])
    def test_missing_input_fails_closed(
        self, holdbacks: ModuleType, inputs: Inputs, missing: str
    ) -> None:
        getattr(inputs, missing).unlink()
        assert holdbacks.main(inputs.argv()) == 1
        assert (inputs.outputs_written(), inputs.out.exists()) == ({}, False)

    def test_log_carries_counts_and_no_ledger_text(
        self, holdbacks: ModuleType, inputs: Inputs, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        inputs.write(release_prs(), LEDGER, {"held": [2], "hold_breaking": False})
        assert holdbacks.main(inputs.argv()) == 0
        assert "2 held (1 by the ledger, 1 by the review only)" in caplog.text
        assert (LEDGER_ID in caplog.text, LEDGER_REASON in caplog.text) == (
            False,
            False,
        )

    def test_reasons_name_the_ledger_entry_or_the_review(
        self, holdbacks: ModuleType, inputs: Inputs, releasedata: ModuleType
    ) -> None:
        notes = releasedata.ReleaseNotes.model_validate_json(
            inputs.notes.read_text("utf-8")
        )
        entries = holdbacks.load_ledger(inputs.ledger)
        assert holdbacks.held_reasons(notes, [1, 2], entries) == {
            1: f"{LEDGER_ID}: {LEDGER_REASON}",
            2: holdbacks.NO_LEDGER_HOLD,
        }
