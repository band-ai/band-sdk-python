from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.conftest import make_message_event
from tests.runtime.helpers import (
    ROOM_ID,
    run_through,
)
from tests.runtime.scheduler.driver import attempt_delivery
from tests.runtime.verdict.kit import (
    ROWS,
    ScriptedAdapter,
    assert_runtime_outcome,
)


class TestEachTurnOutcomeIsReportedHonestly:
    @pytest.mark.parametrize("row", sorted(ROWS))
    async def test_each_turn_outcome_is_reported_honestly(
        self, row: str, link: MagicMock
    ) -> None:
        steps, completes = ROWS[row]
        ctx = run_through(ScriptedAdapter(steps), link)

        await attempt_delivery(
            ctx, make_message_event(room_id=ROOM_ID, sender_id="user-1")
        )

        assert_runtime_outcome(link, completes)
