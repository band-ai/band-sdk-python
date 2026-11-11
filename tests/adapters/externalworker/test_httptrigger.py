"""Without a custom trigger, the adapter nudges its worker with a wake-up-shaped POST."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr
from pytest_httpx import HTTPXMock

from band.adapters.external_worker import (
    ExternalWorkerAdapter,
    ExternalWorkerAdapterConfig,
    WorkRequest,
)
from band.client.streaming import DeliveryStatus
from band.runtime.tools.agent import AgentTools
from tests.adapters.externalworker.kit import run_turn
from tests.runtime.delivery.kit import RestPeer
from tests.runtime.helpers import AGENT_ID, ROOM_ID

pytestmark = pytest.mark.looptime

WORKER_URL = "https://worker.example/run"


class TestHttpTrigger:
    @pytest.mark.parametrize(
        ("token", "authorization"),
        [(SecretStr("routine-secret"), "Bearer routine-secret"), (None, None)],
        ids=["bearer", "no-token"],
    )
    async def test_the_worker_receives_the_wakeup_body_and_its_token(
        self,
        httpx_mock: HTTPXMock,
        rest_peer: RestPeer,
        tools: AgentTools,
        token: SecretStr | None,
        authorization: str | None,
    ) -> None:
        rest_peer.post("ours")

        def worker(request: httpx.Request) -> httpx.Response:
            rest_peer.set_status("ours", DeliveryStatus.PROCESSED)
            return httpx.Response(202)

        httpx_mock.add_callback(worker, url=WORKER_URL, method="POST")
        config = ExternalWorkerAdapterConfig(
            trigger_url=WORKER_URL, trigger_token=token
        )

        await run_turn(
            ExternalWorkerAdapter(config, rest_client=tools.rest),
            tools,
            rest_peer.row("ours"),
        )

        request = httpx_mock.get_request()
        assert request is not None
        assert (json.loads(request.content), request.headers.get("authorization")) == (
            WorkRequest(AGENT_ID, ROOM_ID, "ours").wakeup_body(),
            authorization,
        )

    async def test_a_worker_answering_with_an_error_fails_the_turn(
        self, httpx_mock: HTTPXMock, rest_peer: RestPeer, tools: AgentTools
    ) -> None:
        rest_peer.post("ours")
        httpx_mock.add_response(url=WORKER_URL, method="POST", status_code=503)
        config = ExternalWorkerAdapterConfig(trigger_url=WORKER_URL)

        with pytest.raises(httpx.HTTPStatusError):
            await run_turn(
                ExternalWorkerAdapter(config, rest_client=tools.rest),
                tools,
                rest_peer.row("ours"),
            )

        assert rest_peer.reads == []
