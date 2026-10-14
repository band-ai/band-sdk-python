"""The ordinary prefix a turn consumes, from REST order and retained originals."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

import pytest

from band.runtime.selection import (
    Candidate,
    Disposition,
    MessageKey,
    Selection,
    Stop,
    select_prefix,
)
from tests.conftest import make_message_event
from tests.runtime.batching.kit import EPOCH


def candidate(
    message_id: str,
    second: int,
    disposition: Disposition = Disposition.RUNNABLE,
    *,
    content: str = "",
    naive: bool = False,
) -> Candidate:
    """An original ``second`` seconds after the epoch; ``naive`` drops the UTC offset."""
    moment = EPOCH + timedelta(seconds=second)
    if naive:
        moment = moment.replace(tzinfo=None)
    event = make_message_event(
        msg_id=message_id, content=content or message_id, inserted_at=moment.isoformat()
    )
    return Candidate(MessageKey.of_event(event), event, disposition)


def ids(selection: Selection) -> list[str]:
    return [member.message_id for member in selection.members]


def contents(selection: Selection) -> list[str]:
    return [member.event.payload.content for member in selection.members]


def outcome(selection: Selection) -> tuple[list[str], Stop]:
    return ids(selection), selection.stop


def omitted_original_outcomes(
    rest: Sequence[Candidate], omitted: Candidate
) -> list[tuple[list[str], Stop]]:
    """Selections made before and after the list's first original has run, for a
    delivered original the list omits."""
    return [
        outcome(
            select_prefix(
                listed,
                [omitted],
                cap=10,
                page_reaches=rest[-1].key,
                exhausted=False,
            )
        )
        for listed in (rest, rest[1:])
    ]


def select_at_end(
    rest: Sequence[Candidate],
    retained: Sequence[Candidate] = (),
    *,
    cap: int = 10,
    limit: MessageKey | None = None,
) -> Selection:
    """Select from a REST read that reached the end of the actionable list."""
    return select_prefix(
        rest,
        retained,
        cap=cap,
        page_reaches=None,
        exhausted=True,
        limit=limit,
    )


class TestPrefixSelection:
    def test_rest_order_is_the_turn_order(self) -> None:
        rest = [candidate("a", 1), candidate("b", 2), candidate("c", 3)]

        assert outcome(select_at_end(rest)) == (["a", "b", "c"], Stop.END)

    def test_the_ceiling_ends_the_prefix(self) -> None:
        rest = [candidate("a", 1), candidate("b", 2), candidate("c", 3)]

        assert outcome(select_at_end(rest, cap=2)) == (["a", "b"], Stop.CAP)

    def test_settled_and_failed_originals_are_not_members(self) -> None:
        rest = [
            candidate("a", 1, Disposition.SKIP),
            candidate("b", 2),
            candidate("c", 3, Disposition.SKIP),
        ]

        assert ids(select_at_end(rest)) == ["b"]

    def test_an_owned_original_holds_back_everything_after_it(self) -> None:
        rest = [
            candidate("a", 1),
            candidate("b", 2, Disposition.HELD),
            candidate("c", 3),
        ]

        assert outcome(select_at_end(rest)) == (["a"], Stop.HELD)

    def test_an_original_in_both_sources_counts_once_and_runs_as_delivered(
        self,
    ) -> None:
        listed = candidate("a", 1, content="as the list returned it")
        delivered = candidate("a", 1, content="as the WebSocket delivered it")

        selection = select_at_end([listed], [delivered])

        assert (ids(selection), contents(selection)) == (
            ["a"],
            ["as the WebSocket delivered it"],
        )

    def test_the_lists_failed_status_outranks_the_delivered_original_without_one(
        self,
    ) -> None:
        listed_failed = candidate("a", 1, Disposition.SKIP)
        delivered = candidate("a", 1)

        selection = select_at_end([listed_failed, candidate("b", 2)], [delivered])

        assert ids(selection) == ["b"]

    def test_a_retained_original_the_list_omits_runs_alone_after_earlier_work(
        self,
    ) -> None:
        rest = [candidate("a", 1), candidate("c", 3)]

        assert omitted_original_outcomes(rest, candidate("b", 2)) == [
            (["a"], Stop.BOUNDARY),
            (["b"], Stop.BOUNDARY),
        ]

    @pytest.mark.parametrize(
        ("rest_naive", "delivered_naive"),
        [(True, False), (False, True)],
        ids=["naive-list", "naive-websocket"],
    )
    def test_a_naive_timestamp_is_placed_against_an_aware_one_as_utc(
        self, rest_naive: bool, delivered_naive: bool
    ) -> None:
        rest = [
            candidate("a", 1, naive=rest_naive),
            candidate("c", 3, naive=rest_naive),
        ]
        omitted = candidate("b", 2, naive=delivered_naive)

        assert omitted_original_outcomes(rest, omitted) == [
            (["a"], Stop.BOUNDARY),
            (["b"], Stop.BOUNDARY),
        ]

    def test_a_retained_original_beyond_the_page_waits_for_the_scan_to_reach_it(
        self,
    ) -> None:
        rest = [candidate("a", 1), candidate("b", 2)]
        later = candidate("z", 50)

        selection = select_prefix(
            rest, [later], cap=10, page_reaches=rest[-1].key, exhausted=False
        )

        assert outcome(selection) == (["a", "b"], Stop.FRONTIER)

    def test_a_local_barrier_ends_the_prefix_at_its_segment(self) -> None:
        rest = [candidate("a", 1), candidate("b", 2), candidate("c", 3)]

        assert outcome(select_at_end(rest, limit=rest[1].key)) == (
            ["a", "b"],
            Stop.LIMIT,
        )

    @pytest.mark.parametrize("cap", [1, 2])
    def test_a_boundary_original_alone_is_always_a_valid_turn(self, cap: int) -> None:
        only = candidate("a", 1)

        assert outcome(select_at_end([], [only], cap=cap)) == (["a"], Stop.BOUNDARY)
