"""A session is owed the room transcript until it is restored or seeded."""

from __future__ import annotations

import pytest

from band.runtime.sessionresume import SessionSeed
from tests.runtime.sessionresume.kit import confirmed


class TestSessionSeed:
    @pytest.mark.parametrize(
        ("seed", "replay_due"),
        [
            (SessionSeed(), True),
            (SessionSeed(restored=True), False),
            (confirmed(SessionSeed()), False),
            (confirmed(SessionSeed(restored=True)), False),
        ],
        ids=["fresh", "restored", "fresh-seeded", "restored-prompted"],
    )
    def test_replay_is_due_only_for_an_unseeded_fresh_session(
        self, seed: SessionSeed, replay_due: bool
    ) -> None:
        assert seed.replay_due is replay_due
