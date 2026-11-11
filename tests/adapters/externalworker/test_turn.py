"""A turn nudges the external worker and holds until the platform reports the
message processed, or fails once the deadline passes."""

from __future__ import annotations

import asyncio

import pytest

from band.adapters.external_worker import (
    WORKER_FAILED_CODE,
    WORKER_TIMEOUT_CODE,
    ExternalWorkerAdapterConfig,
    WorkRequest,
)
from band.core.exceptions import ProviderRunError
from band.runtime.tools.agent import AgentTools
from tests.adapters.externalworker.kit import (
    GivingUpWorker,
    Worker,
    connected_adapter,
    run_turn,
)
from tests.runtime.delivery.kit import FAILED_ROWS, RestPeer
from tests.runtime.helpers import AGENT_ID, ROOM_ID

pytestmark = pytest.mark.looptime

DEADLINE = ExternalWorkerAdapterConfig(timeout_seconds=30)


class TestTurn:
    async def test_the_turn_holds_until_the_worker_marks_the_message_processed(
        self, rest_peer: RestPeer, tools: AgentTools, worker: Worker
    ) -> None:
        # Older messages left failed fill more than one page ahead of ours, so
        # neither "oldest outstanding" nor the first page can stand in for it.
        rest_peer.post_failed(FAILED_ROWS)
        rest_peer.post("ours")
        adapter = connected_adapter(tools, trigger=worker)
        turn = asyncio.create_task(run_turn(adapter, tools, rest_peer.row("ours")))

        await rest_peer.page_served(4)  # two full checks, each still pending
        held = not turn.done()
        worker.finish("ours")
        await turn

        assert (held, worker.requests, rest_peer.without_reads()) == (
            True,
            [WorkRequest(AGENT_ID, ROOM_ID, "ours")],
            [],
        )

    async def test_a_message_left_unprocessed_past_the_deadline_fails_the_turn(
        self, rest_peer: RestPeer, tools: AgentTools, worker: Worker
    ) -> None:
        rest_peer.post("ours")
        adapter = connected_adapter(tools, DEADLINE, trigger=worker)

        with pytest.raises(ProviderRunError) as failure:
            await run_turn(adapter, tools, rest_peer.row("ours"))

        assert (failure.value.code, rest_peer.status_of("ours")) == (
            WORKER_TIMEOUT_CODE,
            None,
        )

    async def test_a_message_the_worker_marks_failed_ends_the_turn_at_once(
        self, rest_peer: RestPeer, tools: AgentTools
    ) -> None:
        rest_peer.post("ours")
        adapter = connected_adapter(tools, DEADLINE, trigger=GivingUpWorker(rest_peer))

        with pytest.raises(ProviderRunError) as failure:
            await run_turn(adapter, tools, rest_peer.row("ours"))

        assert (failure.value.code, len(rest_peer.reads)) == (WORKER_FAILED_CODE, 1)

    async def test_a_nudge_that_never_returns_is_bounded_by_the_deadline(
        self, rest_peer: RestPeer, tools: AgentTools
    ) -> None:
        rest_peer.post("ours")
        never = asyncio.Event()

        async def unanswered(request: WorkRequest) -> None:
            await never.wait()

        adapter = connected_adapter(tools, DEADLINE, trigger=unanswered)

        with pytest.raises(ProviderRunError) as failure:
            await run_turn(adapter, tools, rest_peer.row("ours"))

        assert (failure.value.code, rest_peer.reads) == (WORKER_TIMEOUT_CODE, [])

    async def test_a_failed_nudge_fails_the_turn_without_waiting(
        self, rest_peer: RestPeer, tools: AgentTools
    ) -> None:
        rest_peer.post("ours")

        async def refused(request: WorkRequest) -> None:
            raise ConnectionRefusedError("worker is down")

        adapter = connected_adapter(tools, trigger=refused)

        with pytest.raises(ConnectionRefusedError):
            await run_turn(adapter, tools, rest_peer.row("ours"))

        assert rest_peer.reads == []
