"""Structured ask frames preserve native question metadata and answer variants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from band.integrations.omproomask import render_room_questions
from band.integrations.ompwire import OmpAskAnswer, OmpUIRequest, OmpUIResponse


class TestOmpAskWire:
    @pytest.mark.parametrize("recommended", [-1, 0.5, 8])
    def test_numeric_recommendations_do_not_break_the_reader(
        self, recommended: float
    ) -> None:
        request = OmpUIRequest.model_validate(
            {
                "id": "ask-1",
                "method": "ask",
                "questions": [
                    {
                        "id": "question-1",
                        "question": "Pick a database",
                        "options": [{"label": "SQLite", "preview": "schema preview"}],
                        "recommended": recommended,
                    }
                ],
            }
        )
        assert request.questions[0].recommended == recommended
        assert request.questions[0].options[0].preview == "schema preview"
        rendered = render_room_questions(request.questions)
        assert "Pick a database" in rendered
        assert "SQLite" in rendered
        assert "recommended" not in rendered

    @pytest.mark.parametrize(
        "other_answer", [{"value": "SQLite"}, {"confirmed": True}, {"cancelled": True}]
    )
    def test_structured_answers_cannot_be_mixed_with_another_ui_variant(
        self, other_answer: dict[str, str | bool]
    ) -> None:
        with pytest.raises(ValidationError, match="only one answer"):
            OmpUIResponse.model_validate(
                {"answers": [OmpAskAnswer(id="question-1")], **other_answer}
            )
