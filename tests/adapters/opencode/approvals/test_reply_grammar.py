from __future__ import annotations

import pytest

from band.adapters.opencode.approvals.commands import (
    PermissionCommand,
    is_question_rejection,
    parse_permission_reply,
    parse_question_answers,
)


class TestReplyGrammar:
    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            ("approve", PermissionCommand("once", None)),
            ("/Always perm-1", PermissionCommand("always", "perm-1")),
            ("reject Perm-1", PermissionCommand("reject", "Perm-1")),
            ("approve please", PermissionCommand("once", None)),
            ("approve perm-1 please", PermissionCommand("once", "perm-1")),
            ("approved", None),
            ("please approve", None),
            ("   ", None),
        ],
    )
    def test_permission_reply(
        self, content: str, expected: PermissionCommand | None
    ) -> None:
        assert parse_permission_reply(content) == expected

    @pytest.mark.parametrize(
        ("content", "question_count", "expected"),
        [
            ("blue", 1, [["blue"]]),
            ("  ", 1, None),
            ("blue\n\n red \nextra", 2, [["blue"], ["red"]]),
            ("blue", 2, None),
            ("blue", 0, None),
        ],
    )
    def test_question_answers(
        self, content: str, question_count: int, expected: list[list[str]] | None
    ) -> None:
        assert parse_question_answers(content, question_count) == expected

    @pytest.mark.parametrize(
        ("command", "rejects"),
        [("reject", True), ("/REJECT q-1", True), ("approve q-1", False), ("", False)],
    )
    def test_question_rejection(self, command: str, rejects: bool) -> None:
        assert is_question_rejection(command) is rejects
