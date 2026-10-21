from __future__ import annotations

import asyncio

from band.integrations.opencode import parse_opencode_event
from band.integrations.opencode.types import OpencodeSessionState
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    event_permission,
    event_question,
    event_session_error,
    event_session_idle,
    make_platform_message,
    tools_protocol,
    wait_for,
)


class TestAskAbandonment:
    async def test_session_error_abandons_a_pending_permission(
        self, make_adapter, tools
    ) -> None:
        """Regression: every other terminal path (interrupt, timeout, cleanup)
        abandons pending approvals before releasing the turn -- a session error
        must too, or a permission left parked on a human survives the turn
        indefinitely: unreachable once a later turn rebinds room_state.approvals,
        resolvable only by its own expiry timer instead of the human's actual
        reply."""
        fake_client = FakeOpencodeClient(prompt_event_sequences=[[]])
        adapter = make_adapter(fake_client)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        turn_task = asyncio.create_task(
            deliver_message(
                adapter,
                make_platform_message(),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        )
        await wait_for(lambda: bool(fake_client.prompt_calls))

        room_state = await adapter._get_or_create_room_state("room-1")
        turn = room_state.turn
        assert turn is not None

        await adapter._handle_event(
            parse_opencode_event(event_permission("sess-1", "perm-1"))
        )
        assert turn.approvals.awaiting_human()

        # Parking the permission releases on_message's wait; the turn watcher
        # keeps running detached, still awaiting turn_future.
        await turn_task

        await adapter._handle_event(
            parse_opencode_event(event_session_error("sess-1", "boom"))
        )

        assert fake_client.aborted_sessions == ["sess-1"]
        assert not turn.approvals.awaiting_human()

        await adapter.on_cleanup("room-1")

    async def test_session_idle_abandons_a_pending_permission(
        self, make_adapter, tools
    ) -> None:
        """session.idle is a terminal path: a permission still parked on a
        human must be abandoned so a later turn cannot claim it is 'no longer
        pending' while the ask is still live."""
        fake_client = FakeOpencodeClient(prompt_event_sequences=[[]])
        adapter = make_adapter(fake_client)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        turn_task = asyncio.create_task(
            deliver_message(
                adapter,
                make_platform_message(),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        )
        await wait_for(lambda: bool(fake_client.prompt_calls))

        room_state = await adapter._get_or_create_room_state("room-1")
        turn = room_state.turn
        assert turn is not None

        await adapter._handle_event(
            parse_opencode_event(event_permission("sess-1", "perm-1"))
        )
        assert turn.approvals.awaiting_human()
        await turn_task

        await adapter._handle_event(parse_opencode_event(event_session_idle("sess-1")))

        assert fake_client.aborted_sessions == ["sess-1"]
        assert not turn.approvals.awaiting_human()

        await adapter.on_cleanup("room-1")

    async def test_session_error_abandons_a_pending_question(
        self, make_adapter, tools
    ) -> None:
        fake_client = FakeOpencodeClient(prompt_event_sequences=[[]])
        adapter = make_adapter(fake_client)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        turn_task = asyncio.create_task(
            deliver_message(
                adapter,
                make_platform_message(),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        )
        await wait_for(lambda: bool(fake_client.prompt_calls))

        room_state = await adapter._get_or_create_room_state("room-1")
        turn = room_state.turn
        assert turn is not None

        await adapter._handle_event(
            parse_opencode_event(event_question("sess-1", "q-1", "Who?"))
        )
        assert turn.approvals.awaiting_human()
        await turn_task

        await adapter._handle_event(
            parse_opencode_event(event_session_error("sess-1", "boom"))
        )

        assert fake_client.aborted_sessions == ["sess-1"]
        assert not turn.approvals.awaiting_human()

        await adapter.on_cleanup("room-1")

    async def test_session_idle_abandons_a_pending_question(
        self, make_adapter, tools
    ) -> None:
        fake_client = FakeOpencodeClient(prompt_event_sequences=[[]])
        adapter = make_adapter(fake_client)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        turn_task = asyncio.create_task(
            deliver_message(
                adapter,
                make_platform_message(),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        )
        await wait_for(lambda: bool(fake_client.prompt_calls))

        room_state = await adapter._get_or_create_room_state("room-1")
        turn = room_state.turn
        assert turn is not None

        await adapter._handle_event(
            parse_opencode_event(event_question("sess-1", "q-1", "Who?"))
        )
        assert turn.approvals.awaiting_human()
        await turn_task

        await adapter._handle_event(parse_opencode_event(event_session_idle("sess-1")))

        assert fake_client.aborted_sessions == ["sess-1"]
        assert not turn.approvals.awaiting_human()

        await adapter.on_cleanup("room-1")
