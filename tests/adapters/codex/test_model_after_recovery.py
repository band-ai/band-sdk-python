"""Codex adapter: a thread restored after a lost process keeps the room's model."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.adapters.codex import CodexAdapter
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.codex.types import CodexRequestMethod
from tests.adapters.codex.kit import PROCESS_EXITED, CodexRoom, answered_turn
from tests.adapters.codexturns import FakeCodexClient

LISTED_MODEL = "model-a"
CHOSEN_MODEL = "model-b"


class TestModelAfterRecovery:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("commands", "room_model"),
        [
            pytest.param((), LISTED_MODEL, id="listed-at-first-start"),
            pytest.param(
                (f"/model {CHOSEN_MODEL}",), CHOSEN_MODEL, id="chosen-with-model"
            ),
        ],
    )
    async def test_a_restored_thread_keeps_the_rooms_model(
        self,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
        commands: tuple[str, ...],
        room_model: str,
    ) -> None:
        """The model is the room's choice, not the process's: a replacement
        process whose model/list leads with another model still runs the
        restored thread on the model the room was using."""
        lost = FakeCodexClient(
            events=[*answered_turn(), PROCESS_EXITED],
            model_list_result={"data": [{"id": LISTED_MODEL, "hidden": False}]},
        )
        replacement = FakeCodexClient(events=answered_turn())
        room = CodexRoom(await started_codex(lost, replacement, model=None), lost)

        for command in commands:
            await room.send(command)
        await room.send("work")
        with pytest.raises(TurnResultAlreadyReported):
            await room.send("more work")
        await room.send("work again")

        assert CodexRequestMethod.THREAD_RESUME in replacement.request_methods
        (turn,) = replacement.params_of(CodexRequestMethod.TURN_START)
        assert turn["model"] == room_model
