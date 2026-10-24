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

    @pytest.mark.parametrize(
        ("questions", "expected"),
        [
            ([], ""),
            ([OmpAskQuestion(id="q", question="  Where?  ", header="  ")], "Where?"),
            (
                [
                    OmpAskQuestion(
                        id="q",
                        question=" Database? ",
                        header=" Storage ",
                        options=[
                            OmpAskOption(label="SQLite", description=" Embedded "),
                            OmpAskOption(
                                label="Postgres", description="  ", preview=" \n "
                            ),
                        ],
                        recommended=0,
                        multi=True,
                    )
                ],
                (
                    "**Storage:** Database?\n- SQLite: Embedded (recommended)"
                    "\n- Postgres\nPick any that apply."
                ),
            ),
            (
                [
                    OmpAskQuestion(
                        id="q1",
                        question="Database?",
                        options=[OmpAskOption(label="SQLite")],
                    ),
                    OmpAskQuestion(id="q2", question="Deploy where?", multi=True),
                ],
                "1. Database?\n   - SQLite\n\n2. Deploy where?",
            ),
            (
                [
                    OmpAskQuestion(
                        id="patch",
                        question="Which patch?",
                        options=[
                            OmpAskOption(
                                label="A",
                                preview="delete records\n  DROP TABLE records;",
                            )
                        ],
                    )
                ],
                "Which patch?\n- A\n\n  delete records\n    DROP TABLE records;",
            ),
            (
                [
                    OmpAskQuestion(id="first", question="First?"),
                    OmpAskQuestion(
                        id="patch",
                        question="Which patch?",
                        options=[
                            OmpAskOption(
                                label="A",
                                preview="delete records\n  DROP TABLE records;",
                            )
                        ],
                    ),
                ],
                "1. First?\n\n2. Which patch?\n   - A\n\n     delete records\n       DROP TABLE records;",
            ),
        ],
        ids=[
            "empty",
            "plain",
            "described-recommended-multi",
            "numbered",
            "preview",
            "numbered-preview",
        ],
    )
    def test_rendered_layout(
        self, questions: list[OmpAskQuestion], expected: str
    ) -> None:
        assert render_room_questions(questions) == expected

    def test_multi_digit_question_keeps_preview_under_its_option(self) -> None:
        questions = [
            OmpAskQuestion(id=f"q{number}", question="Earlier question")
            for number in range(1, 10)
        ]
        questions.append(
            OmpAskQuestion(
                id="patch",
                question="Which patch?",
                options=[OmpAskOption(label="A", preview="Patch:\n  keep records")],
            )
        )

        rendered = render_room_questions(questions)

        assert rendered.endswith(
            "10. Which patch?\n    - A\n\n      Patch:\n        keep records"
        )
