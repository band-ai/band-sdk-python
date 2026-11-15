from __future__ import annotations

import asyncio

import httpx
import pytest

from band.client.streaming import MessageMetadata
from tests.conftest import make_message_event
from tests.runtime.delivery.kit import RestPeer, started_session
from tests.runtime.helpers import AGENT_ID, ROOM_ID

pytestmark = [pytest.mark.asyncio, pytest.mark.looptime]


class TestQueuedOriginal:
    @pytest.mark.parametrize("response_includes_arrival", [True, False])
    async def test_scan_preserves_queued_socket_original(
        self, response_includes_arrival: bool
    ) -> None:
        peer = RestPeer()
        original_answer = peer.answer
        held = asyncio.Event()
        release = asyncio.Event()
        settled = asyncio.Event()
        reads = 0
        trace: list[str] = []

        async def answer(request: httpx.Request) -> httpx.Response:
            nonlocal reads
            if request.method == "GET" and request.url.path.endswith("/messages"):
                reads += 1
                if reads == 2:
                    response = (
                        None if response_includes_arrival else original_answer(request)
                    )
                    held.set()
                    await release.wait()
                    response = (
                        response if response is not None else original_answer(request)
                    )
                    trace.append(
                        "list response "
                        + str([row["id"] for row in response.json()["data"]])
                    )
                    return response
            response = original_answer(request)
            if request.url.path.endswith("/approve/processed"):
                settled.set()
            return response

        peer.answer = answer
        peer.post("head")
        async with started_session(
            peer, batching=True, accepting=False, controls=frozenset({"approve"})
        ) as session:
            async with asyncio.timeout(5):
                await held.wait()
                assert session.turns.refused
                peer.post("approve")
                original = make_message_event(
                    room_id=ROOM_ID,
                    msg_id="approve",
                    content="approve",
                    sender_id="user-1",
                    inserted_at=peer.rows["approve"].inserted_at.isoformat(),
                    metadata=MessageMetadata(
                        status="sent",
                        mentions=[{"id": AGENT_ID, "username": "socket-agent-handle"}],
                    ),
                )
                expected = original.payload.metadata.model_dump()
                await session.deliver(original)
                trace.append("socket original enqueued")
                release.set()
                await settled.wait()
        assert len(session.controls.payloads) == 1
        actual = session.controls.payloads[0].metadata
        assert actual.model_dump() == expected, {
            "trace": trace,
            "actual": actual.model_dump(),
            "expected": expected,
        }
        assert original.payload.metadata.model_dump() == expected
