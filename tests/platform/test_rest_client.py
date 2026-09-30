"""The generated REST client's wire behavior for optional parameters, which the
"never pass None" rule in AGENTS.md depends on."""

from __future__ import annotations

import json

import httpx

from band.client.rest import AsyncRestClient

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
