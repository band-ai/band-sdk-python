"""Tests for the Slack bridge Starlette server."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from band.integrations.slack.server import build_router
from band.integrations.slack.signature import SLACK_SIGNATURE_VERSION
from band.integrations.slack.types import SlackApp


def _sign(secret: str, body: bytes, timestamp: str) -> str:
    base = f"{SLACK_SIGNATURE_VERSION}:{timestamp}:".encode() + body
    digest = hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return f"{SLACK_SIGNATURE_VERSION}={digest}"


def _make_client(apps: list[SlackApp], dispatcher=None) -> TestClient:
    router = build_router(apps, dispatcher=dispatcher)
    app = Starlette()
    app.mount("/", router)
    return TestClient(app)


def _app(slug: str = "recruit", secret: str = "test-signing-secret") -> SlackApp:
    return SlackApp(
        slug=slug,
        signing_secret=secret,
        bot_token="xoxb-test",
    )


def test_url_verification_returns_challenge():
    app = _app()
    client = _make_client([app])
    body = json.dumps(
        {"type": "url_verification", "challenge": "abc123-challenge"}
    ).encode()
    timestamp = str(int(time.time()))
    signature = _sign(app.signing_secret, body, timestamp)

    response = client.post(
        "/recruit/events",
        content=body,
        headers={
            "x-slack-request-timestamp": timestamp,
            "x-slack-signature": signature,
            "content-type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.text == "abc123-challenge"


def test_invalid_signature_returns_401():
    app = _app()
    client = _make_client([app])
    body = b'{"type":"event_callback"}'
    timestamp = str(int(time.time()))

    response = client.post(
        "/recruit/events",
        content=body,
        headers={
            "x-slack-request-timestamp": timestamp,
            "x-slack-signature": "v0=deadbeefdeadbeef",
            "content-type": "application/json",
        },
    )

    assert response.status_code == 401


def test_missing_signature_header_returns_401():
    app = _app()
    client = _make_client([app])
    body = b'{"type":"event_callback"}'

    response = client.post(
        "/recruit/events",
        content=body,
        headers={
            "x-slack-request-timestamp": str(int(time.time())),
            "content-type": "application/json",
        },
    )

    assert response.status_code == 401


def test_missing_timestamp_header_returns_401():
    app = _app()
    client = _make_client([app])
    body = b'{"type":"event_callback"}'

    response = client.post(
        "/recruit/events",
        content=body,
        headers={
            "x-slack-signature": "v0=deadbeef",
            "content-type": "application/json",
        },
    )

    assert response.status_code == 401


def test_unknown_slug_returns_404():
    app = _app()
    client = _make_client([app])

    response = client.post("/unknown/events", content=b"{}")
    assert response.status_code == 404


def test_valid_event_invokes_dispatcher_and_acks_200():
    app = _app()
    received: list[dict] = []

    async def dispatcher(received_app, payload):
        received.append({"app_slug": received_app.slug, "payload": payload})

    client = _make_client([app], dispatcher=dispatcher)
    body = json.dumps(
        {"type": "event_callback", "event": {"type": "app_mention"}}
    ).encode()
    timestamp = str(int(time.time()))
    signature = _sign(app.signing_secret, body, timestamp)

    response = client.post(
        "/recruit/events",
        content=body,
        headers={
            "x-slack-request-timestamp": timestamp,
            "x-slack-signature": signature,
            "content-type": "application/json",
        },
    )

    assert response.status_code == 200
    assert len(received) == 1
    assert received[0]["app_slug"] == "recruit"
    assert received[0]["payload"]["event"]["type"] == "app_mention"


def test_invalid_json_returns_400_after_signature_passes():
    app = _app()
    client = _make_client([app])
    body = b"not valid json"
    timestamp = str(int(time.time()))
    signature = _sign(app.signing_secret, body, timestamp)

    response = client.post(
        "/recruit/events",
        content=body,
        headers={
            "x-slack-request-timestamp": timestamp,
            "x-slack-signature": signature,
            "content-type": "application/json",
        },
    )

    assert response.status_code == 400


def test_multi_app_routing_uses_per_app_secret():
    app_a = _app(slug="a", secret="secret-a")
    app_b = _app(slug="b", secret="secret-b")
    client = _make_client([app_a, app_b])

    body = json.dumps({"type": "url_verification", "challenge": "ok"}).encode()
    timestamp = str(int(time.time()))

    # Signing for app A but posting to app B's route must fail.
    cross = _sign(app_a.signing_secret, body, timestamp)
    bad_response = client.post(
        "/b/events",
        content=body,
        headers={
            "x-slack-request-timestamp": timestamp,
            "x-slack-signature": cross,
            "content-type": "application/json",
        },
    )
    assert bad_response.status_code == 401

    # And signing with the matching secret on its own route succeeds.
    correct = _sign(app_b.signing_secret, body, timestamp)
    good_response = client.post(
        "/b/events",
        content=body,
        headers={
            "x-slack-request-timestamp": timestamp,
            "x-slack-signature": correct,
            "content-type": "application/json",
        },
    )
    assert good_response.status_code == 200
    assert good_response.text == "ok"


def test_build_router_rejects_empty_apps():
    with pytest.raises(ValueError, match="at least one"):
        build_router([])


def test_build_router_rejects_duplicate_slugs():
    apps = [_app(slug="x"), _app(slug="x")]
    with pytest.raises(ValueError, match="Duplicate"):
        build_router(apps)


def test_dispatcher_exception_does_not_break_ack():
    app = _app()

    async def boom(received_app, payload):
        raise RuntimeError("dispatcher exploded")

    client = _make_client([app], dispatcher=boom)
    body = json.dumps(
        {"type": "event_callback", "event": {"type": "app_mention"}}
    ).encode()
    timestamp = str(int(time.time()))
    signature = _sign(app.signing_secret, body, timestamp)

    response = client.post(
        "/recruit/events",
        content=body,
        headers={
            "x-slack-request-timestamp": timestamp,
            "x-slack-signature": signature,
            "content-type": "application/json",
        },
    )

    # Slack must get a 200 even if our handler crashes downstream; we
    # log the exception and retry semantics live elsewhere.
    assert response.status_code == 200


def test_adapter_router_property_exposes_starlette_router():
    from typing import Any
    from unittest.mock import MagicMock

    from band.core.simple_adapter import SimpleAdapter
    from band.integrations.slack.adapter import SlackAdapter

    class _NoopInner(SimpleAdapter[Any]):
        async def on_message(self, *args: Any, **kwargs: Any) -> None:
            return None

    adapter = SlackAdapter(
        inner=_NoopInner(),
        apps=[_app()],
        rest_client=MagicMock(),
    )
    router = adapter.router
    # Property caches the router instance.
    assert adapter.router is router
