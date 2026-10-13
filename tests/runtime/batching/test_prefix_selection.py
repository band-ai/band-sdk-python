"""The ordinary prefix a turn consumes, from REST order and retained originals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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

EPOCH = datetime(2024, 1, 1, tzinfo=UTC)


def candidate(
    message_id: str, second: int, disposition: Disposition = Disposition.RUNNABLE
) -> Candidate:
    moment = EPOCH + timedelta(seconds=second)
    event = make_message_event(msg_id=message_id, inserted_at=moment.isoformat())
    return Candidate(MessageKey.of_event(event), event, disposition)


def ids(selection: Selection) -> list[str]:
    return [member.message_id for member in selection.members]


class TestPrefixSelection:
    def test_rest_order_is_the_turn_order(self) -> None:
        rest = [candidate("a", 1), candidate("b", 2), candidate("c", 3)]

        selection = select_prefix(rest, [], cap=10, page_reaches=None, exhausted=True)

        assert (ids(selection), selection.stop) == (["a", "b", "c"], Stop.END)

    def test_the_ceiling_ends_the_prefix(self) -> None:
        rest = [candidate("a", 1), candidate("b", 2), candidate("c", 3)]

        selection = select_prefix(rest, [], cap=2, page_reaches=None, exhausted=True)

        assert (ids(selection), selection.stop) == (["a", "b"], Stop.CAP)

    def test_settled_and_failed_originals_are_not_members(self) -> None:
        rest = [
            candidate("a", 1, Disposition.SKIP),
            candidate("b", 2),
            candidate("c", 3, Disposition.SKIP),
        ]

        selection = select_prefix(rest, [], cap=10, page_reaches=None, exhausted=True)

        assert ids(selection) == ["b"]

    def test_an_owned_original_holds_back_everything_after_it(self) -> None:
        rest = [
            candidate("a", 1),
            candidate("b", 2, Disposition.HELD),
            candidate("c", 3),
        ]

        selection = select_prefix(rest, [], cap=10, page_reaches=None, exhausted=True)

        assert (ids(selection), selection.stop) == (["a"], Stop.HELD)

    def test_an_original_in_both_sources_counts_once_and_keeps_its_payload(
        self,
    ) -> None:
        from_rest = candidate("a", 1)
        delivered = candidate("a", 1)

        selection = select_prefix(
            [from_rest], [delivered], cap=10, page_reaches=None, exhausted=True
        )

        assert [m.event for m in selection.members] == [delivered.event]

    def test_a_retained_original_the_list_omits_runs_alone_after_earlier_work(
        self,
    ) -> None:
        rest = [candidate("a", 1), candidate("c", 3)]
        thread_reply = candidate("b", 2)

        first = select_prefix(
            rest, [thread_reply], cap=10, page_reaches=rest[-1].key, exhausted=False
        )
        second = select_prefix(
            [rest[1]],
            [thread_reply],
            cap=10,
            page_reaches=rest[-1].key,
            exhausted=False,
        )

        assert [(ids(s), s.stop) for s in (first, second)] == [
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

        assert (ids(selection), selection.stop) == (["a", "b"], Stop.FRONTIER)

    def test_a_local_barrier_ends_the_prefix_at_its_segment(self) -> None:
        rest = [candidate("a", 1), candidate("b", 2), candidate("c", 3)]

        selection = select_prefix(
            rest, [], cap=10, page_reaches=None, exhausted=True, limit=rest[1].key
        )

        assert (ids(selection), selection.stop) == (["a", "b"], Stop.LIMIT)

    @pytest.mark.parametrize("cap", [1, 2])
    def test_a_boundary_original_alone_is_always_a_valid_turn(self, cap: int) -> None:
        only = candidate("a", 1)

        selection = select_prefix(
            [], [only], cap=cap, page_reaches=None, exhausted=True
        )

        assert (ids(selection), selection.stop) == (["a"], Stop.BOUNDARY)
