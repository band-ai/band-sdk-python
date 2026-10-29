"""The ACP collecting client serializes each session's posts in arrival order."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp.schema import DeclineElicitationResponse

from band.integrations.acp.client_runtime import ACPCollectingClient
from band.integrations.acp.types import CollectedChunk
from tests.integrations.acp.acp_toolkit.sink import NarrationSink
from tests.integrations.acp.clientruntime.concurrency import ConcurrencyProbe


class TestACPCollectingClientSerialization:
    """The acp transport runs each notification as its own task; the client must
    serialize the per-session ingest→sink path so room posts keep causal order,
    and must not lose a sink failure (the transport suppresses handler
    exceptions without a trace)."""

    @pytest.fixture
    def probe(self) -> ConcurrencyProbe:
        return ConcurrencyProbe()

    @staticmethod
    def _tool_call(title: str, call_id: str) -> MagicMock:
        return MagicMock(
            session_update="tool_call",
            title=title,
            tool_call_id=call_id,
            raw_input=None,
            status="in_progress",
        )

    @pytest.mark.asyncio
    async def test_concurrently_dispatched_updates_post_in_arrival_order(
        self, probe: ConcurrencyProbe
    ) -> None:
        """Notifications dispatched as concurrent tasks (the acp dispatcher's
        behavior) must post to the sink one at a time, in wire-arrival order —
        overlapping posts are concurrent REST calls that can commit out of order
        in the room."""
        client = ACPCollectingClient()
        posted: list[str] = []

        async def sink(chunk: CollectedChunk) -> None:
            async with probe:
                posted.append(chunk.content)

        client.set_sink("s1", NarrationSink(sink))

        # Mimic DefaultMessageDispatcher._dispatch_notification: one task per
        # notification, created in wire-arrival order.
        await asyncio.gather(
            *(
                asyncio.create_task(
                    client.session_update("s1", self._tool_call(f"tool-{i}", f"tc-{i}"))
                )
                for i in range(3)
            )
        )

        assert probe.peak == 1  # posts never overlap
        assert posted == ["tool-0", "tool-1", "tool-2"]  # arrival order preserved

    @pytest.mark.asyncio
    async def test_permission_handling_never_interleaves_with_narration_posts(
        self, probe: ConcurrencyProbe
    ) -> None:
        """The permission handler may post a denied tool_call/tool_result pair to
        the room; it must not interleave with in-flight narration posts."""
        client = ACPCollectingClient()

        async def sink(chunk: CollectedChunk) -> None:
            async with probe:
                pass

        async def handler(
            narrate_permission: object | None = None,
            **kwargs: object,
        ) -> dict[str, object]:
            async def denied_posts() -> None:
                async with probe:
                    return

            if narrate_permission is not None:
                await narrate_permission(denied_posts())
            else:
                await denied_posts()
            return {"outcome": {"outcome": "cancelled"}}

        client.set_sink("s1", NarrationSink(sink))
        client.set_permission_handler("s1", handler)

        await asyncio.gather(
            client.session_update("s1", self._tool_call("tool-0", "tc-0")),
            client.request_permission(
                options=[], session_id="s1", tool_call=MagicMock()
            ),
        )

        assert probe.peak == 1

    @pytest.mark.asyncio
    async def test_elicitation_handling_never_interleaves_with_narration_posts(
        self, probe: ConcurrencyProbe
    ) -> None:
        """Elicitation denial narration must serialize under the session lock."""
        client = ACPCollectingClient()

        async def sink(chunk: CollectedChunk) -> None:
            async with probe:
                pass

        async def handler(
            narrate_elicitation: object | None = None,
            **kwargs: object,
        ) -> object:
            async def denied_posts() -> None:
                async with probe:
                    return

            if narrate_elicitation is not None:
                await narrate_elicitation(denied_posts())
            else:
                await denied_posts()
            return DeclineElicitationResponse(action="decline")

        client.set_sink("s1", NarrationSink(sink))
        client.set_elicitation_handler("s1", handler)

        await asyncio.gather(
            client.session_update("s1", self._tool_call("tool-0", "tc-0")),
            client.create_elicitation("Approve?", mode=MagicMock(session_id="s1")),
        )

        assert probe.peak == 1

    @pytest.mark.asyncio
    async def test_create_elicitation_without_handler_declines(self) -> None:
        client = ACPCollectingClient()
        response = await client.create_elicitation(
            "Approve?", mode=MagicMock(session_id="missing")
        )
        assert isinstance(response, DeclineElicitationResponse)
        assert response.action == "decline"

    @pytest.mark.asyncio
    async def test_set_then_reset_clears_elicitation_handler(self) -> None:
        client = ACPCollectingClient()
        handler = AsyncMock(return_value=DeclineElicitationResponse(action="decline"))
        client.set_elicitation_handler("sess-1", handler)
        assert "sess-1" in client._elicitation_handlers
        client.reset_session("sess-1")
        assert "sess-1" not in client._elicitation_handlers

    @pytest.mark.asyncio
    async def test_permission_wait_does_not_block_live_session_updates(self) -> None:
        client = ACPCollectingClient()
        decision_started = asyncio.Event()
        release_decision = asyncio.Event()
        posted: list[str] = []

        async def sink(chunk: CollectedChunk) -> None:
            posted.append(chunk.content)

        async def handler(**kwargs: object) -> dict[str, object]:
            del kwargs
            decision_started.set()
            await release_decision.wait()
            return {"outcome": {"outcome": "cancelled"}}

        client.set_sink("s1", NarrationSink(sink))
        client.set_permission_handler("s1", handler)
        permission = asyncio.create_task(
            client.request_permission(
                options=[], session_id="s1", tool_call=MagicMock()
            )
        )
        await decision_started.wait()

        await client.session_update("s1", self._tool_call("tool-0", "tc-0"))

        assert posted == ["tool-0"]
        release_decision.set()
        await permission

    @pytest.mark.asyncio
    async def test_sink_failure_is_logged_and_keeps_the_chunk_buffered(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A failed room post must not vanish: the acp transport suppresses
        notification-handler exceptions silently, so the client logs the failure
        itself and keeps ingesting (the chunk stays buffered for turn-end
        collection)."""
        client = ACPCollectingClient()

        async def sink(chunk: CollectedChunk) -> None:
            raise RuntimeError("REST post failed")

        client.set_sink("s1", NarrationSink(sink))

        with caplog.at_level(logging.ERROR):
            await client.session_update("s1", self._tool_call("tool-0", "tc-0"))

        chunks = client.get_collected_chunks("s1")
        assert [chunk.content for chunk in chunks] == ["tool-0"]
        assert "Failed to post" in caplog.text
        assert "REST post failed" in caplog.text
