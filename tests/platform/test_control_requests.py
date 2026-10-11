"""Control diagnostics observe requests without replaying or changing errors."""

from __future__ import annotations

import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from band.client.rest import AsyncRestClient
from tests.e2e.baseline.toolkit.user_ops import UserOps

ROOM_ID = "room-1"
SECRET = "private-canary"


@pytest.fixture
def traced_user_ops() -> UserOps:
    return UserOps(
        AsyncRestClient(base_url="https://example.test", api_key=SECRET),
        trace_controls=True,
    )


@pytest.mark.parametrize("operation", ["stop_agent", "play_agent"])
async def test_control_trace_preserves_single_request_and_public_result(
    operation: str,
    traced_user_ops: UserOps,
    httpx_mock: HTTPXMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = json.dumps({"data": {"affected": 1, "private": SECRET}})
    httpx_mock.add_response(
        text=body,
        headers={"x-request-id": "request-1", "x-build-commit": "build-1"},
    )

    result = await getattr(traced_user_ops, operation)(ROOM_ID)

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].method == "POST"
    if operation == "stop_agent":
        assert result is None
    else:
        assert result == body
    assert "status=200 affected=1 request=request-1 build=build-1" in caplog.text
    assert SECRET not in caplog.text


@pytest.mark.parametrize("status", [403, 503])
async def test_control_trace_preserves_http_failure_without_retry(
    status: int,
    traced_user_ops: UserOps,
    httpx_mock: HTTPXMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    httpx_mock.add_response(status_code=status, json={"private": SECRET})

    with pytest.raises(httpx.HTTPStatusError) as error:
        await traced_user_ops.stop_agent(ROOM_ID)

    assert error.value.response.status_code == status
    assert len(httpx_mock.get_requests()) == 1
    assert f"status={status} affected=None" in caplog.text
    assert SECRET not in caplog.text


async def test_control_trace_accepts_non_json_without_changing_play_result(
    traced_user_ops: UserOps,
    httpx_mock: HTTPXMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    httpx_mock.add_response(text=SECRET)

    assert await traced_user_ops.play_agent(ROOM_ID) == SECRET
    assert "status=200 affected=None" in caplog.text
    assert SECRET not in caplog.text
