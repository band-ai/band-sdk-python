"""Codex adapter: a thread restored after a lost process keeps the room's model."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.codex.types import CodexRequestMethod
from tests.adapters.codex.kit import PROCESS_EXITED, answered_turn, send_bootstrap
from tests.adapters.codexturns import (
    FakeCodexClient,
    patch_codex_client,
    turn_completed,
)

ROOM_MODEL = "model-a"


class TestModelAfterRecovery:
    @pytest.mark.asyncio
    async def test_a_restored_thread_keeps_its_model_when_the_new_process_lists_another(
        self,
    ) -> None:
        """The model is the room's choice, not the process's: a replacement
        process whose model/list leads with another model still runs the
        restored thread on the model it started with."""
        lost = FakeCodexClient(
            events=[turn_completed(), PROCESS_EXITED],
            model_list_result={"data": [{"id": ROOM_MODEL, "hidden": False}]},
        )
        replacement = FakeCodexClient(events=answered_turn())
        adapter = CodexAdapter(CodexAdapterConfig(model=None))
        patch_codex_client(adapter, lost, replacement)
        await adapter.on_started("Codex Agent", "A coding agent")

        await send_bootstrap(adapter)
        with pytest.raises(TurnResultAlreadyReported):
            await send_bootstrap(adapter)
        await send_bootstrap(adapter)

        assert CodexRequestMethod.THREAD_RESUME in replacement.request_methods
        (turn,) = replacement.params_of(CodexRequestMethod.TURN_START)
        assert turn["model"] == ROOM_MODEL
        await adapter.cleanup_all()
