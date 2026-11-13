"""Without a custom trigger, the adapter nudges its worker with a wake-up-shaped POST."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr
from pytest_httpx import HTTPXMock

from band.adapters.external_worker import (
    WORKER_UNREACHABLE_CODE,
    ExternalWorkerAdapterConfig,
    WorkRequest,
)
from band.client.streaming import DeliveryStatus
from band.core.exceptions import ProviderRunError
from band.runtime.tools.agent import AgentTools
from tests.adapters.externalworker.kit import connected_adapter, run_turn
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
            connected_adapter(tools, config),
            tools,
            rest_peer.row("ours"),
        )

        request = httpx_mock.get_request()
        assert request is not None
        assert (json.loads(request.content), request.headers.get("authorization")) == (
            WorkRequest(AGENT_ID, ROOM_ID, "ours").wakeup_body(),
            authorization,
        )

    @pytest.mark.parametrize(
        "worker_fails",
        [
            lambda mock: mock.add_response(url=WORKER_URL, status_code=503),
            lambda mock: mock.add_exception(
                httpx.ConnectError("connection refused"), url=WORKER_URL
            ),
        ],
        ids=["error-status", "unreachable"],
    )
    async def test_a_worker_that_cannot_take_the_request_fails_the_turn(
        self,
        httpx_mock: HTTPXMock,
        rest_peer: RestPeer,
        tools: AgentTools,
        worker_fails: Callable[[HTTPXMock], None],
    ) -> None:
        rest_peer.post("ours")
        worker_fails(httpx_mock)
        config = ExternalWorkerAdapterConfig(trigger_url=WORKER_URL)

        with pytest.raises(ProviderRunError) as error:
            await run_turn(
                connected_adapter(tools, config), tools, rest_peer.row("ours")
            )

        assert (error.value.code, rest_peer.reads) == (WORKER_UNREACHABLE_CODE, [])
