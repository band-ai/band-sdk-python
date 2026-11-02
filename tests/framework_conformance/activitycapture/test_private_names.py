"""Activity carries only a kind and, when a cell allows names, only those."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Any

import pytest

from band.runtime.activity import REASONING, tool_call_activity
from band.runtime.tools.types import BandTool
from tests.activitysupport import tool_body
from tests.e2e.baseline.toolkit.activity import (
    EXTRA_DETAIL,
    UNLISTED_TOOL,
    ActivityCapture,
    ActivityPush,
)
from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient
from tests.framework_conformance.activitycapture.topics import TopicPeer

ROOM = "room"
BAND_TOOL = tool_body(BandTool.SEND_MESSAGE)["activity"]
NATIVE_TOOL = tool_body("Bash")["activity"]
ALLOWED = frozenset({BandTool.SEND_MESSAGE})
UNLISTED = pytest.raises(AssertionError, match=UNLISTED_TOOL)
EXTRA_FIELD = pytest.raises(AssertionError, match=EXTRA_DETAIL)


class TestPrivateToolNames:
    @pytest.mark.parametrize(
        ("activity", "tool_names", "expectation"),
        [
            pytest.param(BAND_TOOL, ALLOWED, nullcontext(), id="allowed-name"),
            pytest.param(
                {"kind": REASONING.kind}, ALLOWED, nullcontext(), id="no-tool"
            ),
            pytest.param(NATIVE_TOOL, ALLOWED, UNLISTED, id="unlisted-name"),
            pytest.param(NATIVE_TOOL, None, nullcontext(), id="any-name-without-list"),
            pytest.param(
                {**BAND_TOOL, "args": "x"}, None, EXTRA_FIELD, id="extra-field"
            ),
        ],
    )
    async def test_pushes_carry_only_allowed_fields_and_names(
        self,
        raw_peer: tuple[TrackingWebSocketClient, TopicPeer],
        activity: dict[str, Any],
        tool_names: frozenset[str] | None,
        expectation: AbstractContextManager[object],
    ) -> None:
        ws, peer = raw_peer
        capture = ActivityCapture()
        async with capture.rooms(ws, ROOM):
            await peer.emit(
                ROOM, ActivityPush.CHANGED, {"agent_id": "agent", "activity": activity}
            )
        with expectation:
            capture.assert_private(tool_names=tool_names)

    @pytest.mark.parametrize(
        ("detail", "expectation"),
        [
            pytest.param(BandTool.SEND_MESSAGE, nullcontext(), id="allowed-name"),
            pytest.param("Bash", UNLISTED, id="unlisted-name"),
        ],
    )
    async def test_dispatched_reports_carry_only_allowed_names(
        self, detail: str, expectation: AbstractContextManager[object]
    ) -> None:
        capture = ActivityCapture()
        capture.dispatched(ROOM, "agent", True, tool_call_activity(detail))
        with expectation:
            capture.assert_private(tool_names=ALLOWED)
