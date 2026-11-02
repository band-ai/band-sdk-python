"""Raw activity pushes may name only the tools a cell allows."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Any

import pytest

from tests.e2e.baseline.toolkit.activity import ActivityCapture, ActivityPush
from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient
from tests.framework_conformance.activitycapture.topics import TopicPeer

ROOM = "room"
ALLOWED = frozenset({"band_send_message"})
REJECTED = pytest.raises(AssertionError)


class TestPrivateToolNames:
    @pytest.mark.parametrize(
        ("activity", "expectation"),
        [
            pytest.param(
                {"kind": "tool_call", "name": "band_send_message"},
                nullcontext(),
                id="allowed-name",
            ),
            pytest.param({"kind": "reasoning"}, nullcontext(), id="no-tool"),
            pytest.param(
                {"kind": "tool_call", "name": "Bash"}, REJECTED, id="native-name"
            ),
            pytest.param(
                {"kind": "tool_call", "name": "band_send_message", "args": "x"},
                REJECTED,
                id="extra-field",
            ),
        ],
    )
    async def test_pushes_name_only_allowed_tools(
        self,
        raw_peer: tuple[TrackingWebSocketClient, TopicPeer],
        activity: dict[str, Any],
        expectation: AbstractContextManager[object],
    ) -> None:
        ws, peer = raw_peer
        capture = ActivityCapture()
        async with capture.rooms(ws, ROOM):
            await peer.emit(
                ROOM, ActivityPush.CHANGED, {"agent_id": "agent", "activity": activity}
            )
        with expectation:
            capture.assert_private(tool_names=ALLOWED)
