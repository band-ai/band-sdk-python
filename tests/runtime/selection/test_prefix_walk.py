from __future__ import annotations

import pytest

from band.runtime.selection import (
    Candidate,
    Disposition,
    Selection,
    Stop,
    select_prefix,
)
from tests.runtime.selection.kit import candidate, key_at

RUNNABLE, SKIP, HELD, CONTROL, BOUNDARY = (
    Disposition.RUNNABLE,
    Disposition.SKIP,
    Disposition.HELD,
    Disposition.CONTROL,
    Disposition.BOUNDARY,
)


def outline(selection: Selection) -> tuple[list[str], Stop, str | None]:
    """The members' ids, why the prefix ended, and the id that ended it."""
    ended_by = selection.at.message_id if selection.at is not None else None
    return ([c.message_id for c in selection.members], selection.stop, ended_by)


def walk(
    candidates: list[Candidate],
    *,
    listed: set[str],
    read_to: int | None = None,
    exhausted: bool = False,
    resumed: set[str] | None = None,
    cap: int = 2,
) -> Selection:
    """Walk ``candidates`` against a REST read that reached ``read_to`` seconds."""
    return select_prefix(
        candidates,
        listed=listed,
        cap=cap,
        page_reaches=None if read_to is None else key_at(read_to, "page"),
        exhausted=exhausted,
        resumed=resumed or (),
    )


class TestPrefixWalk:
    @pytest.mark.parametrize(
        ("candidates", "expected"),
        [
            pytest.param(
                [candidate("a", second=1), candidate("b", second=2)],
                (["a", "b"], Stop.CAP, None),
                id="stops-at-the-cap",
            ),
            pytest.param(
                [candidate("a", second=1)],
                (["a"], Stop.END, None),
                id="runs-out-of-candidates",
            ),
            pytest.param(
                [candidate("a", SKIP, second=1), candidate("b", second=2)],
                (["b"], Stop.END, None),
                id="passes-over-a-skipped-message",
            ),
            pytest.param(
                [candidate("a", second=1), candidate("b", HELD, second=2)],
                (["a"], Stop.HELD, "b"),
                id="waits-at-a-held-claim",
            ),
            pytest.param(
                [candidate("a", HELD, second=1), candidate("b", second=2)],
                ([], Stop.HELD, "a"),
                id="a-leading-held-claim-selects-nothing",
            ),
            pytest.param(
                [candidate("a", second=1), candidate("b", CONTROL, second=2)],
                (["a"], Stop.CONTROL, "b"),
                id="a-control-never-joins-a-batch",
            ),
            pytest.param(
                [candidate("a", CONTROL, second=1), candidate("b", second=2)],
                (["a"], Stop.CONTROL, "a"),
                id="a-leading-control-runs-alone",
            ),
            pytest.param(
                [candidate("a", second=1), candidate("b", BOUNDARY, second=2)],
                (["a"], Stop.BOUNDARY, "b"),
                id="a-reclassified-control-ends-the-prefix",
            ),
        ],
    )
    def test_listed_messages(
        self,
        candidates: list[Candidate],
        expected: tuple[list[str], Stop, str | None],
    ) -> None:
        listed = {c.message_id for c in candidates}

        assert outline(walk(candidates, listed=listed)) == expected

    @pytest.mark.parametrize(
        ("read_to", "exhausted", "resumed", "expected"),
        [
            pytest.param(
                1, False, None, ([], Stop.FRONTIER, "r"), id="read-short-of-it"
            ),
            pytest.param(None, False, None, ([], Stop.FRONTIER, "r"), id="empty-read"),
            pytest.param(
                5, False, None, (["r"], Stop.BOUNDARY, "r"), id="read-passed-it"
            ),
            pytest.param(
                None, True, None, (["r"], Stop.BOUNDARY, "r"), id="read-ended"
            ),
            pytest.param(
                1, False, {"r"}, (["r"], Stop.BOUNDARY, "r"), id="resumed-retry"
            ),
        ],
    )
    def test_retained_original_absent_from_the_read(
        self,
        read_to: int | None,
        exhausted: bool,
        resumed: set[str] | None,
        expected: tuple[list[str], Stop, str | None],
    ) -> None:
        retained = candidate("r", second=3)

        selection = walk(
            [retained],
            listed=set(),
            read_to=read_to,
            exhausted=exhausted,
            resumed=resumed,
        )

        assert outline(selection) == expected

    def test_an_unmatched_original_ends_a_prefix_that_already_has_members(self) -> None:
        listed = candidate("a", second=1)
        retained = candidate("r", second=2)

        selection = walk([listed, retained], listed={"a"}, read_to=9)

        assert outline(selection) == (["a"], Stop.BOUNDARY, "r")
