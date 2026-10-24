from __future__ import annotations

from unittest.mock import MagicMock

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


class TestAnExemptAdapterIsNeverJudged:
    async def test_an_exempt_adapter_is_never_judged(self, link: MagicMock) -> None:
        ctx = run_through(ScriptedAdapter([], judges=False), link)

        await attempt_delivery(
            ctx, make_message_event(room_id=ROOM_ID, sender_id="user-1")
        )

        link.mark_processed.assert_awaited_once()
        assert runtime_failures(link) == []
