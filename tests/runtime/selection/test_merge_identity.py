from __future__ import annotations

from band.runtime.selection import Disposition, merge
from tests.runtime.selection.kit import candidate


class TestMergeIdentity:
    def test_an_original_in_both_sources_appears_once_in_rest_order(self) -> None:
        rest = [candidate("a", second=1), candidate("b", second=2)]
        retained = [candidate("b", second=2), candidate("c", second=3)]

        merged = merge(rest, retained)

        assert [c.message_id for c in merged] == ["a", "b", "c"]

    def test_the_delivered_payload_wins_and_rest_owns_position_and_status(self) -> None:
        from_rest = candidate(
            "a", Disposition.SKIP, second=1, observed_metadata={"status": "failed"}
        )
        delivered = candidate("a", Disposition.RUNNABLE, second=9)

        (merged,) = merge([from_rest], [delivered])

        assert merged.event is delivered.event
        assert (merged.key, merged.disposition, merged.observed_metadata) == (
            from_rest.key,
            Disposition.SKIP,
            {"status": "failed"},
        )

    def test_a_message_the_read_omitted_keeps_its_own_facts(self) -> None:
        only_retained = candidate("a", second=4)

        assert merge([], [only_retained]) == [only_retained]
