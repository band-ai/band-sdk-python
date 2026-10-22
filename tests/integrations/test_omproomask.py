"""Native recommendation metadata cannot hide a room's question or options."""

from __future__ import annotations

import pytest

from band.integrations.omproomask import render_room_questions
from band.integrations.ompwire import OmpAskOption, OmpAskQuestion


class TestOmpRoomQuestions:
    @pytest.mark.parametrize("recommended", [-1, 0.5, 8])
    def test_invalid_recommendation_preserves_question_and_options(
        self, recommended: float
    ) -> None:
        question = OmpAskQuestion(
            id="database-choice",
            question="Pick a database",
            options=[OmpAskOption(label="SQLite", preview="schema preview")],
            recommended=recommended,
        )

        rendered = render_room_questions([question])

        assert "Pick a database" in rendered
        assert "SQLite" in rendered
        assert "recommended" not in rendered
