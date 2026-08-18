"""CredentialResolver: the SDK half of the delegated-credential exchange (INT-993).

The wire contract under test is the frozen platform endpoint
``POST /api/v1/agent/delegation/token`` (agent API key in ``X-API-Key``; errors
shaped ``{"error": {"code", "message", "request_id"}}``). ``band_rest`` 0.0.26
has no delegation client, so the resolver hand-rolls the httpx POST off
``BandLink``'s ``rest_url``/``api_key`` — interception here is the same global
``pytest-httpx`` ``httpx_mock`` fixture ``test_link_credentials.py`` uses, so
the observed request is the real outbound call, not an injected seam.

Reuse policy pinned here (RFC 6749 no-store; the platform replies
``cache-control: no-store``): tokens live in resolver memory only, keyed by
audience, fresh while ``expires_at`` clears a 30 s skew, and are NEVER reused
when ``expires_at`` is null — null means unknown, not expired, and the SDK
must not fabricate a TTL.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from pytest_httpx import HTTPXMock

from band.core.delegation import DelegationEnvelope
from band.platform.delegation_exchange import (
    AudienceNotAllowed,
    ConsentMissing,
    CredentialResolver,
    CrossOrgBlocked,
    DelegatedToken,
    DelegationDenied,
    DelegationError,
    DelegationUnavailable,
    ExchangeRateLimited,
    NoDelegation,
    ProviderNotConnected,
    Revoked,
    WindowExpired,
)
from band.platform.link import BandLink

REST_URL = "https://platform.test"
EXCHANGE_URL = f"{REST_URL}/api/v1/agent/delegation/token"
MESSAGE_ID = "8f7f2f6a-3f6e-4a3e-9b1a-2c4d5e6f7a8b"
AUDIENCE = "1f2e3d4c-5b6a-7980-a1b2-c3d4e5f6a7b8"


def make_link() -> BandLink:
    return BandLink(agent_id="agent-1", api_key="sk-agent-key", rest_url=REST_URL)


def make_envelope(message_id: str | None = MESSAGE_ID) -> DelegationEnvelope:
    return DelegationEnvelope.model_validate(
        {
            "version": 1,
            "originator": {
                "uuid": "0a1b2c3d-4e5f-6071-8293-a4b5c6d7e8f9",
                "handle": "alice",
                "display_name": "Alice",
            },
            "message_id": message_id,
            "minted_at": "2026-08-14T00:00:00Z",
        }
    )


def make_resolver(
    envelope: DelegationEnvelope | None = None,
) -> CredentialResolver:
    return CredentialResolver(make_link(), envelope)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def token_body(
    *,
    expires_at: str | None,
    access_token: str = "provider-token-1",
) -> dict[str, object]:
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_at": expires_at,
        "obo": {
            "originator_uuid": "0a1b2c3d-4e5f-6071-8293-a4b5c6d7e8f9",
            "originator_handle": "alice",
            "agent_uuid": "agent-1",
            "audience": AUDIENCE,
        },
    }


def error_body(code: str, message: str = "nope") -> dict[str, object]:
    return {"error": {"code": code, "message": message, "request_id": "req-123"}}


class TestWireRequest:
    """The outbound POST matches the frozen contract exactly."""

    async def test_posts_message_id_and_audience_with_api_key_header(
        self, httpx_mock: HTTPXMock
    ) -> None:
        expires = _iso(datetime.now(timezone.utc) + timedelta(hours=1))
        httpx_mock.add_response(json=token_body(expires_at=expires))

        resolver = make_resolver(make_envelope())
        await resolver.token_for(AUDIENCE)

        request = httpx_mock.get_request()
        assert request is not None
        assert request.method == "POST"
        assert str(request.url) == EXCHANGE_URL
        assert request.headers["X-API-Key"] == "sk-agent-key"
        # Missing Content-Type is a 422 on the platform side — pin it.
        assert request.headers["Content-Type"] == "application/json"
        import json

        assert json.loads(request.content) == {
            "message_id": MESSAGE_ID,
            "audience": AUDIENCE,
        }

    async def test_parses_a_success_response(self, httpx_mock: HTTPXMock) -> None:
        expires = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=1)
        httpx_mock.add_response(json=token_body(expires_at=_iso(expires)))

        resolver = make_resolver(make_envelope())
        token = await resolver.token_for(AUDIENCE)

        assert isinstance(token, DelegatedToken)
        assert token.access_token == "provider-token-1"
        assert token.token_type == "bearer"
        assert token.expires_at == expires
        assert token.obo is not None
        assert token.obo.originator_uuid == "0a1b2c3d-4e5f-6071-8293-a4b5c6d7e8f9"
        assert token.obo.originator_handle == "alice"
        assert token.obo.agent_uuid == "agent-1"
        # The platform echoes the RESOLVED canonical connector id.
        assert token.obo.audience == AUDIENCE

    async def test_malformed_success_body_raises_typed_error(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(json={"unexpected": "shape"})

        resolver = make_resolver(make_envelope())
        with pytest.raises(DelegationError):
            await resolver.token_for(AUDIENCE)


class TestReusePolicy:
    """In-memory only, expiry-aware with 30 s skew, never on null expiry."""

    async def test_fresh_token_is_reused_without_a_second_request(
        self, httpx_mock: HTTPXMock
    ) -> None:
        expires = _iso(datetime.now(timezone.utc) + timedelta(hours=1))
        httpx_mock.add_response(json=token_body(expires_at=expires))

        resolver = make_resolver(make_envelope())
        first = await resolver.token_for(AUDIENCE)
        second = await resolver.token_for(AUDIENCE)

        assert first is second
        assert len(httpx_mock.get_requests()) == 1

    async def test_null_expiry_is_never_reused(self, httpx_mock: HTTPXMock) -> None:
        # null = UNKNOWN, not expired: each call must fetch a fresh token
        # rather than fabricate a TTL for reuse.
        httpx_mock.add_response(json=token_body(expires_at=None, access_token="tok-a"))
        httpx_mock.add_response(json=token_body(expires_at=None, access_token="tok-b"))

        resolver = make_resolver(make_envelope())
        first = await resolver.token_for(AUDIENCE)
        second = await resolver.token_for(AUDIENCE)

        assert first.access_token == "tok-a"
        assert second.access_token == "tok-b"
        assert len(httpx_mock.get_requests()) == 2

    async def test_expired_token_is_refetched(self, httpx_mock: HTTPXMock) -> None:
        expired = _iso(datetime.now(timezone.utc) - timedelta(minutes=5))
        fresh = _iso(datetime.now(timezone.utc) + timedelta(hours=1))
        httpx_mock.add_response(json=token_body(expires_at=expired))
        httpx_mock.add_response(
            json=token_body(expires_at=fresh, access_token="tok-fresh")
        )

        resolver = make_resolver(make_envelope())
        await resolver.token_for(AUDIENCE)
        second = await resolver.token_for(AUDIENCE)

        assert second.access_token == "tok-fresh"
        assert len(httpx_mock.get_requests()) == 2

    async def test_token_inside_the_skew_window_is_refetched(
        self, httpx_mock: HTTPXMock
    ) -> None:
        # Still nominally live (+10 s) but inside the 30 s skew: treat as
        # expired so a token cannot die mid-provider-call.
        nearly = _iso(datetime.now(timezone.utc) + timedelta(seconds=10))
        fresh = _iso(datetime.now(timezone.utc) + timedelta(hours=1))
        httpx_mock.add_response(json=token_body(expires_at=nearly))
        httpx_mock.add_response(
            json=token_body(expires_at=fresh, access_token="tok-fresh")
        )

        resolver = make_resolver(make_envelope())
        await resolver.token_for(AUDIENCE)
        second = await resolver.token_for(AUDIENCE)

        assert second.access_token == "tok-fresh"
        assert len(httpx_mock.get_requests()) == 2

    async def test_audiences_cache_independently(self, httpx_mock: HTTPXMock) -> None:
        other_audience = "9e8d7c6b-5a49-3827-b1a0-f9e8d7c6b5a4"
        expires = _iso(datetime.now(timezone.utc) + timedelta(hours=1))
        httpx_mock.add_response(json=token_body(expires_at=expires, access_token="a"))
        httpx_mock.add_response(json=token_body(expires_at=expires, access_token="b"))

        resolver = make_resolver(make_envelope())
        first = await resolver.token_for(AUDIENCE)
        second = await resolver.token_for(other_audience)

        assert first.access_token == "a"
        assert second.access_token == "b"
        assert len(httpx_mock.get_requests()) == 2

    async def test_a_new_resolver_does_not_inherit_tokens(
        self, httpx_mock: HTTPXMock
    ) -> None:
        # No-store means memory-of-this-resolver only: nothing on disk, nothing
        # shared between resolver instances.
        expires = _iso(datetime.now(timezone.utc) + timedelta(hours=1))
        httpx_mock.add_response(json=token_body(expires_at=expires))
        httpx_mock.add_response(json=token_body(expires_at=expires))

        await make_resolver(make_envelope()).token_for(AUDIENCE)
        await make_resolver(make_envelope()).token_for(AUDIENCE)

        assert len(httpx_mock.get_requests()) == 2


class TestSingleFlight:
    async def test_concurrent_calls_for_one_audience_make_one_request(
        self, httpx_mock: HTTPXMock
    ) -> None:
        # The per-message exchange cap (default 20) makes a tool fan-out
        # stampede expensive: concurrent token_for calls must coalesce. The
        # callback suspends on a real await so all five tasks genuinely
        # overlap — a plain add_response resolves without yielding, which
        # would serialize the tasks and mask a missing lock.
        expires = _iso(datetime.now(timezone.utc) + timedelta(hours=1))

        async def slow_token(request: object) -> object:
            import httpx

            await asyncio.sleep(0.01)
            return httpx.Response(200, json=token_body(expires_at=expires))

        httpx_mock.add_callback(slow_token)

        resolver = make_resolver(make_envelope())
        tokens = await asyncio.gather(*(resolver.token_for(AUDIENCE) for _ in range(5)))

        assert len(httpx_mock.get_requests()) == 1
        assert all(token is tokens[0] for token in tokens)


class TestErrorTaxonomy:
    """Every frozen error code maps to its typed exception."""

    @pytest.mark.parametrize(
        ("status", "code", "expected"),
        [
            (403, "window_expired", WindowExpired),
            (403, "consent_missing", ConsentMissing),
            (403, "provider_not_connected", ProviderNotConnected),
            (403, "revoked", Revoked),
            (403, "audience_not_allowed", AudienceNotAllowed),
            (403, "cross_org_blocked", CrossOrgBlocked),
            (403, "delegation_denied", DelegationDenied),
            (429, "rate_limited", ExchangeRateLimited),
            (404, "not_found", DelegationUnavailable),
        ],
    )
    async def test_maps_code_to_typed_error(
        self,
        httpx_mock: HTTPXMock,
        status: int,
        code: str,
        expected: type[DelegationError],
    ) -> None:
        httpx_mock.add_response(status_code=status, json=error_body(code))

        resolver = make_resolver(make_envelope())
        with pytest.raises(expected) as excinfo:
            await resolver.token_for(AUDIENCE)

        assert excinfo.value.code == code
        assert excinfo.value.status == status
        assert excinfo.value.request_id == "req-123"
        assert "nope" in str(excinfo.value)

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            # Fern's auth plug rejects without the error envelope.
            (401, DelegationError),
            (404, DelegationUnavailable),
            (429, ExchangeRateLimited),
        ],
    )
    async def test_falls_back_by_http_status_without_a_known_code(
        self,
        httpx_mock: HTTPXMock,
        status: int,
        expected: type[DelegationError],
    ) -> None:
        httpx_mock.add_response(status_code=status, text="denied")

        resolver = make_resolver(make_envelope())
        with pytest.raises(expected) as excinfo:
            await resolver.token_for(AUDIENCE)

        assert excinfo.value.status == status

    async def test_unknown_code_stays_a_delegation_error(
        self, httpx_mock: HTTPXMock
    ) -> None:
        # forbidden (wrong credential type) and validation_error (422) have no
        # dedicated class; they surface as the typed base with code intact.
        httpx_mock.add_response(status_code=403, json=error_body("forbidden"))

        resolver = make_resolver(make_envelope())
        with pytest.raises(DelegationError) as excinfo:
            await resolver.token_for(AUDIENCE)

        assert type(excinfo.value) is DelegationError
        assert excinfo.value.code == "forbidden"

    async def test_errors_are_not_cached(self, httpx_mock: HTTPXMock) -> None:
        expires = _iso(datetime.now(timezone.utc) + timedelta(hours=1))
        httpx_mock.add_response(status_code=403, json=error_body("consent_missing"))
        httpx_mock.add_response(json=token_body(expires_at=expires))

        resolver = make_resolver(make_envelope())
        with pytest.raises(ConsentMissing):
            await resolver.token_for(AUDIENCE)
        token = await resolver.token_for(AUDIENCE)

        assert token.access_token == "provider-token-1"
        assert len(httpx_mock.get_requests()) == 2


class TestFirstRunFunnel:
    """The funnel pair carries the contract's remediation, ready to render."""

    async def test_consent_missing_carries_the_grant_remediation(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(
            status_code=403,
            json=error_body("consent_missing", "No consent for this agent"),
        )

        resolver = make_resolver(make_envelope())
        with pytest.raises(ConsentMissing) as excinfo:
            await resolver.token_for(AUDIENCE)

        error = excinfo.value
        assert error.remediation == "Ask the user to grant consent, then retry."
        # str(e) is what a tool-error path shows the model/human — the
        # remediation must ride along with the platform's own message.
        assert "No consent for this agent" in str(error)
        assert error.remediation in str(error)

    async def test_provider_not_connected_carries_the_connect_remediation(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(
            status_code=403,
            json=error_body("provider_not_connected", "No usable connection"),
        )

        resolver = make_resolver(make_envelope())
        with pytest.raises(ProviderNotConnected) as excinfo:
            await resolver.token_for(AUDIENCE)

        error = excinfo.value
        assert error.remediation == "Ask the user to connect the provider, then retry."
        assert "No usable connection" in str(error)
        assert error.remediation in str(error)


class TestNoDelegation:
    """I3: the resolver never runs without an envelope."""

    async def test_none_envelope_raises_before_any_request(
        self, httpx_mock: HTTPXMock
    ) -> None:
        resolver = make_resolver(None)

        with pytest.raises(NoDelegation) as excinfo:
            await resolver.token_for(AUDIENCE)

        assert len(httpx_mock.get_requests()) == 0
        # The absence-error explains itself instead of letting the exchange 404.
        assert "delegation" in str(excinfo.value).lower()

    async def test_envelope_without_message_id_raises_before_any_request(
        self, httpx_mock: HTTPXMock
    ) -> None:
        resolver = make_resolver(make_envelope(message_id=None))

        with pytest.raises(NoDelegation):
            await resolver.token_for(AUDIENCE)

        assert len(httpx_mock.get_requests()) == 0
