"""HTTP forwarding preserves payloads, authentication and transport ownership."""

from __future__ import annotations

import json

import httpx
import pytest
from bridge_core.config import HTTPTarget
from bridge_core.forwarder import HTTPForwarder
from pytest_httpx import HTTPXMock


class TestHTTPForwarder:
    @pytest.mark.parametrize(
        "token, expected_authorization",
        [(None, None), ("test-bearer", "Bearer test-bearer")],
    )
    async def test_posts_authenticated_json_with_injected_client(
        self, token: str | None, expected_authorization: str | None
    ) -> None:
        requests: list[httpx.Request] = []
        target = HTTPTarget(url="https://example.com/invocations", bearer_token=token)
        payload = {
            "event_type": "message_created",
            "room_id": "r1",
            "payload": {"content": "hello"},
        }

        def receive(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
            forwarder = HTTPForwarder(target, httpx_client=client)
            await forwarder.forward(payload)
            await forwarder.close()
            assert not client.is_closed
        assert len(requests) == 1
        assert requests[0].method == "POST"
        assert str(requests[0].url) == target.url
        assert json.loads(requests[0].content) == payload
        assert requests[0].headers.get("Authorization") == expected_authorization
        assert requests[0].extensions["timeout"] == dict.fromkeys(
            ("connect", "read", "write", "pool"), target.timeout
        )

    @pytest.mark.parametrize("status", [401, 500, 503])
    async def test_propagates_unsuccessful_response(self, status: int) -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(status))
        ) as client:
            forwarder = HTTPForwarder(
                HTTPTarget(url="https://example.com"), httpx_client=client
            )
            with pytest.raises(httpx.HTTPStatusError) as error:
                await forwarder.forward({})
        assert error.value.response.status_code == status

    @pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError])
    async def test_propagates_transport_failure(
        self, error_type: type[httpx.TransportError]
    ) -> None:
        def receive(request: httpx.Request) -> httpx.Response:
            raise error_type("transport failure", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
            forwarder = HTTPForwarder(
                HTTPTarget(url="https://example.com"), httpx_client=client
            )
            with pytest.raises(error_type):
                await forwarder.forward({})

    async def test_closes_owned_client_idempotently(self) -> None:
        forwarder = HTTPForwarder(HTTPTarget(url="https://example.com"))
        client = forwarder._get_client()
        await forwarder.close()
        await forwarder.close()
        assert client.is_closed

    async def test_owned_client_reopens_after_close(
        self, httpx_mock: HTTPXMock
    ) -> None:
        target = HTTPTarget(url="https://example.com/invocations")
        httpx_mock.add_response(url=target.url, is_reusable=True)
        forwarder = HTTPForwarder(target)
        await forwarder.forward({})
        await forwarder.close()
        await forwarder.forward({})
        await forwarder.close()
        assert len(httpx_mock.get_requests()) == 2

    async def test_owned_client_sends_bearer(self, httpx_mock: HTTPXMock) -> None:
        target = HTTPTarget(
            url="https://example.com/invocations", bearer_token="owned-bearer"
        )
        httpx_mock.add_response(url=target.url)
        forwarder = HTTPForwarder(target)
        try:
            await forwarder.forward({"payload": {"id": "owned"}})
        finally:
            await forwarder.close()
        request = httpx_mock.get_request()
        assert request is not None
        assert request.headers["authorization"] == "Bearer owned-bearer"
        assert json.loads(request.content) == {"payload": {"id": "owned"}}
