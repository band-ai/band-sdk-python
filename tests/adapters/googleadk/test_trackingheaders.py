"""ADK's tracking headers reach Google from the adapter's own client."""

from __future__ import annotations

from typing import Any

from band.testing import FakeAgentTools
from tests.adapters.genaikit import text_reply
from tests.adapters.googleadk.conftest import ScriptedAdapter
from tests.adapters.googleadk.httppeer import GoogleHTTPPeer

ADK_LABEL = "google-adk/"


class TestTrackingHeaders:
    async def test_generation_request_carries_adk_tracking_headers(
        self, google_http: GoogleHTTPPeer, scripted_adapter: ScriptedAdapter
    ) -> None:
        adapter: Any = await scripted_adapter(text_reply("Done"))

        await adapter.on_turn(
            [], FakeAgentTools(), [], None, None, is_session_bootstrap=True, room_id="r"
        )

        assert [
            ADK_LABEL in request.headers["x-goog-api-client"]
            for request in google_http.requests
        ] == [True]
