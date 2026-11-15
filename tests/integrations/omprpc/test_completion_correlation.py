"""Only evidence belonging to the active prompt can complete it."""

from __future__ import annotations

import pytest

from band.integrations.ompwire import OmpEventType
from tests.integrations.omppeers.base import COMPLETE
from tests.integrations.omprpc.events import EventRecorder, follow_up_texts
from tests.integrations.omprpc.sessions import RpcTurn
from tests.integrations.ompscenarios import PeerScenario


class TestCompletionCorrelation:
    @pytest.mark.parametrize(
        "rpc_turn",
        [PeerScenario.STANDARD_TURN, PeerScenario.LATE_STALE_RESULT],
        indirect=True,
    )
    async def test_current_and_follow_up_turns_deliver_only_their_own_result(
        self, rpc_turn: RpcTurn[EventRecorder]
    ) -> None:
        turn = rpc_turn
        await turn.running
        assert turn.observer.assistant_texts == [COMPLETE]
        assert turn.observer.outline[-1] == OmpEventType.AGENT_END
        assert await follow_up_texts(turn.client) == [COMPLETE]
