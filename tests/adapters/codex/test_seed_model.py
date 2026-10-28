"""Generated Codex turns retain undelivered room context until completion."""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import example, given, settings

from tests.adapters.codex.seedmodel import play, sequences
from tests.adapters.codex.seedpeer import Step


class TestSeedModel:
    @settings(max_examples=25, deadline=None)
    @given(steps=sequences)
    @example(steps=[Step.UPDATE, Step.FAIL, Step.UPDATE, Step.FAIL, Step.COMPLETE])
    @example(steps=[Step.UPDATE, Step.CANCEL, Step.UPDATE, Step.INTERRUPT])
    @example(steps=[Step.POST_FAILS, Step.COMPLETE, Step.FAIL, Step.POST_RECOVERS])
    def test_room_history_context_and_records_follow_completed_turns(
        self, steps: list[Step], tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        asyncio.run(play(steps, tmp_path_factory.mktemp("codex-seed")))
