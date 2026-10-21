from __future__ import annotations

from unittest.mock import MagicMock

from band.core.types import (
    SYNTHETIC_CONTACT_EVENTS_SENDER_ID,
    SYNTHETIC_SENDER_TYPE,
)
from tests.conftest import make_message_event
from tests.runtime.helpers import (
    ROOM_ID,
    run_through,
    runtime_failures,
)
from tests.runtime.scheduler.driver import attempt_delivery
from tests.runtime.verdict.kit import (
    ScriptedAdapter,
)


class TestAContactHubTurnIsNeverJudged:
    async def test_a_contact_hub_turn_is_never_judged(self, link: MagicMock) -> None:
        ctx = run_through(ScriptedAdapter([]), link)

        await attempt_delivery(
            ctx,
            make_message_event(
                room_id=ROOM_ID,
                sender_id=SYNTHETIC_CONTACT_EVENTS_SENDER_ID,
                sender_type=SYNTHETIC_SENDER_TYPE,
            ),
        )

        assert runtime_failures(link) == []
        link.mark_failed.assert_not_awaited()
