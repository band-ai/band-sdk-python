"""The generated REST client's wire behavior for optional parameters, which the
"never pass None" rule in AGENTS.md depends on."""

from __future__ import annotations

import json

import httpx
import pytest
from band_rest.core.api_error import ApiError
from band_rest.core.parse_error import ParsingError

from band.client.rest import AsyncRestClient
from band.core.exceptions import RoomExecutionStoppedError
from band.platform.message_lifecycle import MessageLifecycle
from tests.runtime.helpers import rest_client_over

APPROVED = {
    "data": {
        "id": "req-1",
        "status": "approved",
        "inserted_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
}


async def sent_body(**optional: object) -> dict[str, object]:
    """The JSON body that goes over the wire for a contact-request response."""
    bodies: list[dict[str, object]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=APPROVED)

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        client = AsyncRestClient(
            api_key="test", base_url="https://example.test", httpx_client=http
        )
        await client.agent_api_contacts.respond_to_agent_contact_request(
            action="approve", request_id="req-1", **optional
        )
    return bodies[0]


async def test_none_is_sent_as_null():
    assert (await sent_body(handle=None))["handle"] is None


async def test_an_omitted_optional_stays_off_the_wire():
    assert "handle" not in await sent_body()


@pytest.mark.parametrize(
    "operation", ["mark_processing", "mark_processed", "mark_failed"]
)
async def test_stopped_lifecycle_mark_is_refused(operation: str) -> None:
    async with rest_client_over(lambda _: httpx.Response(204)) as rest:
        method = getattr(MessageLifecycle(), operation)
        args = (rest, "room-1", "msg-1")
        if operation == "mark_failed":
            args += ("failure",)
        with pytest.raises(RoomExecutionStoppedError):
            await method(*args)


@pytest.mark.parametrize("status", [200, 401, 403, 404, 503])
async def test_empty_response_is_not_quiet(status: int) -> None:
    async with rest_client_over(lambda _: httpx.Response(status)) as rest:
        with pytest.raises(ApiError):
            await MessageLifecycle().get_next_message(rest, "room-1")


@pytest.mark.parametrize("status", [200, 401, 403, 404, 503])
async def test_empty_response_cannot_accept_a_claim(status: int) -> None:
    async with rest_client_over(lambda _: httpx.Response(status)) as rest:
        assert not await MessageLifecycle().mark_processing(rest, "room-1", "msg-1")


async def test_next_204_is_quiet() -> None:
    async with rest_client_over(lambda _: httpx.Response(204)) as rest:
        assert await MessageLifecycle().get_next_message(rest, "room-1") is None


@pytest.mark.parametrize(
    "operation,status",
    [
        ("mark_processing", "processing"),
        ("mark_processed", "processed"),
        ("mark_failed", "failed"),
    ],
)
@pytest.mark.parametrize("success", [True, False])
async def test_mark_requires_success_data(
    operation: str, status: str, success: bool
) -> None:
    body = {
        "data": {
            "id": "msg-1",
            "status": status,
            "attempt_number": 1,
            "success": success,
        }
    }
    async with rest_client_over(lambda _: httpx.Response(200, json=body)) as rest:
        method = getattr(MessageLifecycle(), operation)
        args = (rest, "room-1", "msg-1")
        if operation == "mark_failed":
            args += ("failure",)
        assert await method(*args) is success


async def test_malformed_next_success_is_not_quiet() -> None:
    async with rest_client_over(
        lambda _: httpx.Response(200, json={"data": {}})
    ) as rest:
        with pytest.raises(ParsingError):
            await MessageLifecycle().get_next_message(rest, "room-1")
