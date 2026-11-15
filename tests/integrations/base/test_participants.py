"""Emit participant rosters only until their delivery is committed."""

from __future__ import annotations

import pytest

from band.integrations.base import check_and_format_participants
from band.runtime.execution import ExecutionContext
from band.runtime.formatters import build_participants_message


@pytest.mark.usefixtures("httpx_mock")
class TestParticipantNotifications:
    def test_emits_once_and_emits_again_after_a_roster_change(
        self, context: ExecutionContext, alice: dict[str, str]
    ) -> None:
        context.set_participants([alice])

        assert check_and_format_participants(context) == build_participants_message(
            [alice]
        )
        assert check_and_format_participants(context) is None

        bob = {"id": "user-2", "name": "Bob", "type": "User", "handle": "bob"}
        context.add_participant(bob)

        assert check_and_format_participants(context) == build_participants_message(
            [alice, bob]
        )
        assert check_and_format_participants(context) is None

    def test_uncommitted_roster_remains_available_until_sent(
        self, context: ExecutionContext, alice: dict[str, str]
    ) -> None:
        context.set_participants([alice])
        expected = build_participants_message([alice])

        assert check_and_format_participants(context, mark_sent=False) == expected
        assert check_and_format_participants(context, mark_sent=False) == expected
        assert check_and_format_participants(context) == expected
        assert check_and_format_participants(context) is None

    def test_emits_an_empty_roster_once(self, context: ExecutionContext) -> None:
        assert check_and_format_participants(context) == build_participants_message([])
        assert check_and_format_participants(context) is None
