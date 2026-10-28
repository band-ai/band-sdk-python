"""History ends before the earliest current input, even when REST omits its id."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from band.core.types import PlatformMessage
from band.runtime.formatters import messages_before
from tests.roomhistory import original, stored

EARLIER = datetime(2024, 1, 1, tzinfo=UTC)
CURRENT = EARLIER + timedelta(seconds=1)
LATER = CURRENT + timedelta(seconds=1)


class TestMessagesBefore:
    @pytest.mark.parametrize(
        "originals",
        [
            (original("trigger", CURRENT),),
            (original("pending", LATER), original("trigger", CURRENT)),
        ],
        ids=["single", "batch"],
    )
    def test_stored_originals_use_the_exact_prefix_without_timestamps(
        self, originals: tuple[PlatformMessage, ...]
    ) -> None:
        earlier = stored("earlier", inserted_at=None)
        history = [
            earlier,
            stored("current", message_id="trigger", inserted_at=None),
            stored("pending", message_id="pending", inserted_at=None),
        ]

        assert messages_before(history, originals) == [earlier]

    @pytest.mark.parametrize(
        "originals",
        [
            (original("absent", CURRENT),),
            (original("pending", LATER), original("absent", CURRENT)),
        ],
        ids=["missing", "missing-earliest-and-stored-later"],
    )
    def test_an_omitted_earliest_original_still_excludes_pending_turns(
        self, originals: tuple[PlatformMessage, ...]
    ) -> None:
        earlier = stored("earlier", inserted_at=EARLIER)
        history = [
            earlier,
            stored(
                "pending between inputs",
                message_id="between",
                inserted_at=CURRENT + timedelta(microseconds=1),
            ),
            stored("pending", message_id="pending", inserted_at=LATER),
        ]

        assert messages_before(history, originals) == [earlier]

    def test_an_unstored_new_input_after_the_history_keeps_the_history(self) -> None:
        history = [stored("earlier", inserted_at=EARLIER)]

        assert messages_before(history, (original("new", CURRENT),)) == history

    def test_a_first_original_leaves_no_history(self) -> None:
        history = [stored("current", message_id="trigger"), stored("pending")]

        assert messages_before(history, (original("trigger", CURRENT),)) == []

    def test_equal_timestamps_follow_the_platform_id_tiebreak(self) -> None:
        earlier_id = "00000000-0000-4000-8000-000000000001"
        current_id = "00000000-0000-4000-8000-000000000002"
        later_id = "00000000-0000-4000-8000-000000000003"
        earlier = stored("earlier", message_id=earlier_id, inserted_at=CURRENT)
        later = stored("pending", message_id=later_id, inserted_at=CURRENT)

        assert messages_before([earlier, later], (original(current_id, CURRENT),)) == [
            earlier
        ]

    @pytest.mark.parametrize(
        "inserted_at",
        [
            EARLIER,
            EARLIER.isoformat(),
            EARLIER.replace(tzinfo=None),
            EARLIER.astimezone(timezone(timedelta(hours=2))).isoformat(),
        ],
        ids=["datetime", "iso-string", "naive-utc", "offset"],
    )
    def test_timestamp_shapes_keep_the_same_chronological_prefix(
        self, inserted_at: datetime | str
    ) -> None:
        earlier = stored("earlier", inserted_at=inserted_at)
        pending = stored("pending", inserted_at=LATER)

        assert messages_before([earlier, pending], (original("missing", CURRENT),)) == [
            earlier
        ]

    @pytest.mark.parametrize(
        "invalid", [None, "not-a-date", 42], ids=["null", "malformed", "wrong-type"]
    )
    def test_unclassifiable_history_is_unavailable_when_an_original_is_omitted(
        self, invalid: Any
    ) -> None:
        history = [{**stored("unknown time"), "inserted_at": invalid}]

        with pytest.raises(ValueError, match="Cannot order room history"):
            messages_before(history, (original("missing", CURRENT),))

    def test_no_current_inputs_leave_the_history_unchanged(self) -> None:
        history = [stored("earlier", inserted_at=None)]

        assert messages_before(history, ()) == history

    @pytest.mark.parametrize(
        "invalid_id", [None, "", 42], ids=["missing", "empty", "wrong-type"]
    )
    def test_an_unmatched_row_needs_a_valid_id_to_establish_its_order(
        self, invalid_id: Any
    ) -> None:
        history = [
            {**stored("unknown identity", inserted_at=EARLIER), "id": invalid_id}
        ]

        with pytest.raises(ValueError, match="Cannot order room history"):
            messages_before(history, (original("missing", CURRENT),))

    def test_unclassifiable_rows_after_the_fence_are_never_read(self) -> None:
        earlier = stored("earlier", inserted_at=EARLIER)
        pending = stored("pending", inserted_at=LATER)
        history = [earlier, pending, stored("unknown later row", inserted_at=None)]

        assert messages_before(history, (original("missing", CURRENT),)) == [earlier]
