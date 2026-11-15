"""Attributed manual waits retain permission through actual answer I/O."""

from __future__ import annotations

import asyncio
from typing import Literal

import pytest

from band.adapters.opencode import OpencodeAdapterConfig
from band.adapters.opencode.approvals.wording import REJECTED_PERMISSION_FEEDBACK
from tests.adapters.opencode.activity.kit import (
    CALL,
    MESSAGE,
    SESSION,
    OpenActivityRoom,
)
from tests.adapters.opencode.helpers import (
    APPROVAL_REPLY,
    PERMISSION_ID,
    HeldPermissionReplyClient,
    event_message_updated,
    event_permission,
    event_question,
    handle_control,
    room_reply,
)


class TestPermissionActivity:
    @pytest.mark.parametrize(
        ("tool", "waiting", "events"),
        [
            (
                {"messageID": MESSAGE, "callID": CALL},
                True,
                [{"waiting": True}, {"waiting": False}],
            ),
            ({"messageID": "unknown-message", "callID": CALL}, False, []),
            (None, False, []),
        ],
        ids=["attributed", "unknown-message", "no-tool-origin"],
    )
    async def test_manual_permission_keeps_only_its_proven_origin_through_reply_io(
        self,
        activity_room: OpenActivityRoom,
        tool: dict[str, str] | None,
        waiting: bool,
        events: list[dict[str, bool]],
    ) -> None:
        async with activity_room(HeldPermissionReplyClient) as room:
            client = room.client
            assert isinstance(client, HeldPermissionReplyClient)
            await room.publish(
                event_message_updated(SESSION, MESSAGE, parent_id=room.prompt_id),
                event_permission(SESSION, PERMISSION_ID, tool=tool),
            )
            assert room.recorder.waiting is waiting
            resolving = asyncio.create_task(
                handle_control(room.adapter, room_reply(APPROVAL_REPLY))
            )
            try:
                await client.reply_in_flight.wait()
                assert room.recorder.waiting is waiting
                client.release_reply.set()
                assert await resolving
            finally:
                client.release_reply.set()
                await asyncio.gather(resolving, return_exceptions=True)
            assert room.recorder.events == events
            assert not room.recorder.waiting
            assert client.permission_replies == [
                {"permission_id": PERMISSION_ID, "reply": "once"}
            ]

    @pytest.mark.parametrize(
        ("tool", "events"),
        [
            (
                {"messageID": MESSAGE, "callID": CALL},
                [{"waiting": True}, {"waiting": False}],
            ),
            ({"messageID": "unknown-message", "callID": CALL}, []),
        ],
        ids=["attributed", "unknown-message"],
    )
    async def test_manual_question_uses_the_same_proven_message_origin(
        self,
        activity_room: OpenActivityRoom,
        tool: dict[str, str],
        events: list[dict[str, bool]],
    ) -> None:
        async with activity_room() as room:
            await room.publish(
                event_message_updated(SESSION, MESSAGE, parent_id=room.prompt_id),
                event_question(SESSION, "question", "Which result?", tool=tool),
            )
            assert await handle_control(room.adapter, room_reply("blue"))
            assert room.recorder.events == events
            assert room.client.question_replies == [
                {"request_id": "question", "answers": [["blue"]]}
            ]

    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            ("auto_accept", {"permission_id": PERMISSION_ID, "reply": "once"}),
            (
                "auto_decline",
                {
                    "permission_id": PERMISSION_ID,
                    "reply": "reject",
                    "message": REJECTED_PERMISSION_FEEDBACK,
                },
            ),
        ],
        ids=["accepted", "declined"],
    )
    async def test_automatic_permission_never_enters_a_manual_lease_during_io(
        self,
        activity_room: OpenActivityRoom,
        mode: Literal["auto_accept", "auto_decline"],
        expected: dict[str, str],
    ) -> None:
        async with activity_room(
            HeldPermissionReplyClient,
            config=OpencodeAdapterConfig(approval_mode=mode),
        ) as room:
            client = room.client
            assert isinstance(client, HeldPermissionReplyClient)
            await room.publish(
                event_message_updated(SESSION, MESSAGE, parent_id=room.prompt_id)
            )
            publishing = asyncio.create_task(
                room.publish(
                    event_permission(
                        SESSION,
                        PERMISSION_ID,
                        tool={"messageID": MESSAGE, "callID": CALL},
                    )
                )
            )
            try:
                await client.reply_in_flight.wait()
                assert room.recorder.events == []
                assert not room.recorder.waiting
            finally:
                client.release_reply.set()
                await publishing
            assert room.recorder.events == []
            assert client.permission_replies == [expected]
