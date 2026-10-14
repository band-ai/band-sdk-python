"""OneShotInvoker: surfacing room participants to the adapter."""

from __future__ import annotations

from band.runtime.formatters import build_participants_message
from tests.runtime.conftest import make_link_mock, platform_msg
from tests.runtime.oneshot.kit import make_adapter_mock, make_invoker, msg_body

# ---------------------------------------------------------------------------
# Participant roster surfaced to the model
#
# The long-running path treats "no prior roster sent yet" as changed (see
# ExecutionContext.participants_changed, which delegates to
# band_sdk_core.ParticipantRoster.changed() -- True before the first
# mark_sent()), so a room's very first message already carries the roster via
# `participants_msg`. OneShotInvoker has no cross-call state to diff
# against, so every invocation is that same "first time" case — it should
# always build and pass the current roster, never a hardcoded None.
# ---------------------------------------------------------------------------


class TestParticipantsSurfaced:
    async def test_participants_msg_reflects_current_roster(self) -> None:
        participants = [
            {"id": "user-1", "name": "Alice", "type": "User", "handle": "alice"},
            {"id": "agent-2", "name": "Bot2", "type": "Agent", "handle": "bot2"},
        ]
        link = make_link_mock(
            participants=participants,
            next_messages=[platform_msg("msg-1"), None],
        )
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)

        await invoker.handle_event(msg_body())

        adapter.on_event.assert_awaited_once()
        inp = adapter.received_input
        assert inp.participants_msg == build_participants_message(participants)

    async def test_participants_msg_none_when_room_has_no_other_participants(
        self,
    ) -> None:
        """Even an empty roster is a deliberate 'no one else here' statement,
        not an absent one — the model shouldn't have to call a tool to learn
        it's alone in the room.
        """
        link = make_link_mock(
            participants=[],
            next_messages=[platform_msg("msg-1"), None],
        )
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)

        await invoker.handle_event(msg_body())

        inp = adapter.received_input
        assert inp.participants_msg == build_participants_message([])
