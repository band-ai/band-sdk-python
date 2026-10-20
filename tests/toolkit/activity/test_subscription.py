from __future__ import annotations

import pytest
from phoenix_channels_python_client.exceptions import PHXTopicError

from band.client.streaming import WebSocketClient
from band.testing import JoinOutcome, fake_phoenix_server
from tests.e2e.baseline.toolkit.activity import ActivityEvent, activity_topic
from tests.e2e.baseline.toolkit.capture import reply_capture


class TestActivitySubscription:
    async def test_joins_before_trigger_dispatches_and_leaves(self) -> None:
        async with (
            fake_phoenix_server() as server,
            WebSocketClient(ws_url=server.url, api_key="test", agent_id=None) as ws,
        ):
            async with reply_capture(
                ws, "room", observe_activity=True, deadline_s=1
            ) as capture:
                topic = activity_topic("room")
                assert topic in server.joined_topics
                await server.push(
                    topic,
                    ActivityEvent.CHANGED,
                    {
                        "agent_id": "agent",
                        "working": True,
                        "activity": {"kind": "tool_call", "name": "Bash"},
                    },
                )
                await capture.wait_for_tool_activity("agent", {"Bash"})
                mark = capture.activities.snapshot()
                await server.push(
                    topic,
                    ActivityEvent.STOPPED,
                    {"agent_id": "agent", "working": False},
                )
                await capture.wait_for_activity_stop("agent", since=mark)
            assert topic not in ws.joined_topics()
            assert "chat_room:room" not in ws.joined_topics()

    async def test_rejected_activity_join_fails_and_releases_ordinary_capture(
        self,
    ) -> None:
        topic = activity_topic("room")
        async with (
            fake_phoenix_server(
                join_outcomes={topic: [JoinOutcome.REJECTED]}
            ) as server,
            WebSocketClient(ws_url=server.url, api_key="test", agent_id=None) as ws,
        ):
            with pytest.raises(PHXTopicError):
                async with reply_capture(ws, "room", observe_activity=True):
                    pytest.fail(
                        "Rejected activity subscription must not run a scenario"
                    )
            assert "chat_room:room" not in ws.joined_topics()
