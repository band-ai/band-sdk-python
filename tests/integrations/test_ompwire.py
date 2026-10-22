"""Structured ask frames preserve native metadata and mutually exclusive answers."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from band.integrations.ompwire import OmpUIRequest, OmpUIResponse


class TestOmpAskWire:
    def test_structured_questions_preserve_native_metadata(self) -> None:
        question = {
            "id": "database-choice",
            "question": "Which database should this workspace use?",
            "header": "Database",
            "options": [
                {
                    "label": "SQLite",
                    "description": "A local database",
                    "preview": "CREATE TABLE workspace (id INTEGER);",
                }
            ],
            "multi": True,
            "recommended": 0,
        }
        request = OmpUIRequest.model_validate(
            {"id": "ask-1", "method": "ask", "questions": [question]}
        )

        assert request.model_dump(mode="json", exclude_none=True)["questions"] == [
            question
        ]

    @pytest.mark.parametrize("recommended", [-1, 0.5, 8])
    def test_numeric_recommendations_do_not_break_the_reader(
        self, recommended: float
    ) -> None:
        request = OmpUIRequest.model_validate(
            {
                "id": "ask-2",
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

    def test_structured_answers_preserve_an_empty_native_selection(self) -> None:
        answers = [{"id": "question-2", "selectedOptions": []}]
        response = OmpUIResponse.model_validate({"answers": answers})

        assert response.model_dump(mode="json", exclude_none=True)["answers"] == answers

    @pytest.mark.parametrize(
        "other_answer", [{"value": "SQLite"}, {"confirmed": True}, {"cancelled": True}]
    )
    def test_structured_answers_cannot_be_mixed_with_another_ui_variant(
        self, other_answer: dict[str, str | bool]
    ) -> None:
        with pytest.raises(ValidationError, match="only one answer"):
            OmpUIResponse.model_validate(
                {
                    "answers": [{"id": "question-3", "selectedOptions": []}],
                    **other_answer,
                }
            )
