"""A message's position is comparable whichever source reported its timestamp."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from band.runtime.selection import MessageKey
from tests.conftest import make_message_event
from tests.runtime.batching.kit import EPOCH
from tests.runtime.conftest import platform_msg


class TestMessageKey:
    def test_a_naive_rest_timestamp_is_ordered_against_an_aware_delivery_as_utc(
        self,
    ) -> None:
        delivered = MessageKey.of_event(
            make_message_event(
                msg_id="delivered",
                inserted_at=(EPOCH + timedelta(seconds=1)).isoformat(),
            )
        )
        listed_later = MessageKey.of_message(
            replace(
                platform_msg("listed"),
                created_at=(EPOCH + timedelta(seconds=2)).replace(tzinfo=None),
            )
        )

        assert [key.message_id for key in sorted([listed_later, delivered])] == [
            "delivered",
            "listed",
        ]
