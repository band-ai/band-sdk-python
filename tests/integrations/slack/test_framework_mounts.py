"""Framework-mount integration tests (Step 9 absorbed into Step 12).

The PRD lists "Framework compatibility (Python): mounts cleanly into
FastAPI, Flask, Starlette" as a success metric. Flask is out of v1
scope (sync-only, would require asgiref). Starlette is exercised
implicitly throughout ``test_wrapping.py`` via ``ASGITransport`` on
``SlackAdapter.router`` itself — which IS a Starlette ``Router``, so
those tests already prove the Starlette path.

This file pins the remaining claim: ``SlackAdapter.router`` survives
being mounted under a path prefix in a FastAPI app, with signature
verification, request body handling, and background dispatch all
working as they do under direct invocation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from thenvoi.integrations.slack.adapter import SlackAdapter
from thenvoi.integrations.slack.signature import SLACK_SIGNATURE_VERSION
from thenvoi.integrations.slack.types import SlackApp

from tests.integrations.slack.test_wrapping import (
    _SlackReplyBrain,
    _make_rest_mock,
    _mention_event,
)


def _signed_headers(body: bytes, secret: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    base = f"{SLACK_SIGNATURE_VERSION}:{timestamp}:".encode() + body
    digest = hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return {
        "x-slack-request-timestamp": timestamp,
        "x-slack-signature": f"{SLACK_SIGNATURE_VERSION}={digest}",
        "content-type": "application/json",
    }


@pytest.mark.asyncio
async def test_router_mounts_into_fastapi_with_path_prefix():
    """`app.mount("/slack", slack.router)` is the canonical user-facing
    integration. Verify the prefix is stripped correctly, the signed
    event verifies, and the brain is invoked with the synthesized
    platform message — i.e., nothing about the mount interferes with
    the pipeline."""
    app_config = SlackApp(
        slug="dev", bot_token="xoxb-dev", signing_secret="test-secret"
    )
    inner = _SlackReplyBrain(reply=None)
    rest = _make_rest_mock(["room-1"])
    adapter = SlackAdapter(
        inner=inner,
        apps=[app_config],
        api_key="k",
        rest_client=rest,
        web_client_factory=lambda a: AsyncMock(
            chat_postMessage=AsyncMock(return_value={"ok": True, "ts": "x"}),
            assistant_threads_setStatus=AsyncMock(return_value={"ok": True}),
            conversations_replies=AsyncMock(return_value={"messages": []}),
        ),
    )
    adapter._thenvoi_agent_id = "bridge-uuid"  # type: ignore[attr-defined]
    await adapter.on_started("MyBot", "")

    fastapi_app = FastAPI()
    fastapi_app.mount("/slack", adapter.router)

    payload = _mention_event(
        channel="C1", ts="100.0", text="<@U001> hello", user="U999"
    )
    body = json.dumps(payload).encode()
    headers = _signed_headers(body, app_config.signing_secret)

    transport = ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/slack/dev/events", content=body, headers=headers)

    assert response.status_code == 200
    await adapter.wait_idle()

    # Brain saw the event with the right content, proving the full
    # mounted path delivered.
    assert isinstance(inner, _SlackReplyBrain)
    assert len(inner.invocations) == 1
    assert inner.invocations[0]["msg"].content == "<@U001> hello"


@pytest.mark.asyncio
async def test_unsigned_request_to_mounted_fastapi_is_rejected():
    """A mount shouldn't accidentally relax signature verification."""
    app_config = SlackApp(
        slug="dev", bot_token="xoxb-dev", signing_secret="test-secret"
    )
    inner = _SlackReplyBrain(reply=None)
    rest = _make_rest_mock(["room-1"])
    adapter = SlackAdapter(
        inner=inner,
        apps=[app_config],
        api_key="k",
        rest_client=rest,
        web_client_factory=lambda a: AsyncMock(),
    )
    adapter._thenvoi_agent_id = "bridge-uuid"  # type: ignore[attr-defined]
    await adapter.on_started("MyBot", "")

    fastapi_app = FastAPI()
    fastapi_app.mount("/slack", adapter.router)

    payload: dict[str, Any] = {"type": "event_callback", "event": {}}
    body = json.dumps(payload).encode()

    transport = ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/slack/dev/events",
            content=body,
            headers={"content-type": "application/json"},  # no signature
        )

    assert response.status_code == 401
    assert inner.invocations == []
