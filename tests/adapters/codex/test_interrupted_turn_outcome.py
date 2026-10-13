"""Codex adapter: an interrupted turn is settled by the notice that reports it."""

from __future__ import annotations

import pytest

from tests.adapters.codex.kit import run_codex_turn
from tests.adapters.codexturns import turn_completed


class TestInterruptedTurnOutcome:
    """An interrupted turn is settled by the notice that reports it."""

    @pytest.mark.asyncio
    async def test_an_interrupted_turn_is_settled_by_its_notice(self) -> None:
        """The notice answers the room, so the turn is not also a missing reply."""
        turn = await run_codex_turn(events=[turn_completed(status="interrupted")])

        assert turn.tools.chat == ["I stopped before completing this request."]
        assert turn.tools.turn.complete
