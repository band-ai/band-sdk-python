"""An automatic reply or notice addresses each distinct original sender once."""

from __future__ import annotations

from band.core.types import distinct_sender_ids
from tests.runtime.conftest import platform_msg


class TestReplyAudience:
    def test_senders_are_distinct_in_order_of_first_appearance(self) -> None:
        originals = (
            platform_msg("m1", sender_id="alice"),
            platform_msg("m2", sender_id="bob"),
            platform_msg("m3", sender_id="alice"),
        )

        assert distinct_sender_ids(originals) == ("alice", "bob")

    def test_a_message_without_a_sender_adds_no_audience(self) -> None:
        originals = (
            platform_msg("m1", sender_id=""),
            platform_msg("m2", sender_id="bob"),
        )

        assert distinct_sender_ids(originals) == ("bob",)
