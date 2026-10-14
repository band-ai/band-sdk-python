"""A turn's originals keep their own identity; a singleton turn is the default."""

from __future__ import annotations

from typing import Any

from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from band.testing import FakeAgentTools
from tests.runtime.batching.kit import ROOM_ID
from tests.runtime.conftest import platform_msg


def turn_input(msg: PlatformMessage, **fields: Any) -> AgentInput:
    return AgentInput(
        msg=msg,
        tools=FakeAgentTools(),
        history=HistoryProvider(raw=[]),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id=ROOM_ID,
        **fields,
    )


class TestAgentInput:
    def test_a_turn_without_explicit_originals_is_the_singleton_of_its_message(
        self,
    ) -> None:
        only = platform_msg("m1")

        assert turn_input(only).messages == (only,)
        assert turn_input(only, messages=()).messages == (only,)
