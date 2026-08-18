"""Delegated-credential exchange: act with the ASKER's credentials (INT-993).

Given the identity envelope the platform mints on cross-owner messages
(``ctx.delegation``, INT-992), :class:`CredentialResolver` calls the frozen
platform endpoint ``POST /api/v1/agent/delegation/token`` and returns a
short-lived provider token for a named audience — the MCP connector id on the
consent allowlist, not a name or URL.

``band_rest`` 0.0.26 has no delegation client, so the POST is hand-rolled with
httpx off ``BandLink``'s ``rest_url``/``api_key`` (the same ``X-API-Key``
custody slot the Fern client uses). Revisit at M3 if the client group is
regenerated — this module is one file to delete.

Reuse policy (RFC 6749 no-store; the platform replies ``cache-control:
no-store``): tokens are held in resolver memory only, keyed by audience,
reused while ``expires_at`` clears :data:`EXPIRY_SKEW_SECONDS`, and NEVER
reused when ``expires_at`` is null — null means the provider's expiry is
unknown, not that the token is expired, and the SDK must not fabricate a TTL.
A per-audience ``asyncio.Lock`` single-flights concurrent ``token_for`` calls
so a tool fan-out cannot stampede the platform's per-message exchange cap
(default 20).

Error taxonomy mirrors the frozen contract: every wire ``error.code`` maps to
a typed :class:`DelegationError` subclass; unknown codes fall back by HTTP
status. The ``consent_missing`` / ``provider_not_connected`` pair is the
first-run funnel — those classes carry the contract's remediation string, and
``str(error)`` includes it so a rendered tool error already tells the human
what to do next.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel, ConfigDict

from band.core.exceptions import BandError

if TYPE_CHECKING:
    from band.core.delegation import DelegationEnvelope
    from band.platform.link import BandLink

logger = logging.getLogger(__name__)

#: Path of the frozen exchange endpoint, relative to ``BandLink.rest_url``.
EXCHANGE_PATH = "/api/v1/agent/delegation/token"

#: A cached token is treated as expired this many seconds early, so a token
#: cannot die between the freshness check and the provider call that uses it.
EXPIRY_SKEW_SECONDS = 30.0

_REQUEST_TIMEOUT_SECONDS = 30.0


# --- Typed errors (one per frozen wire code + the client-side absence case) ---


class DelegationError(BandError):
    """Base for delegated-credential exchange failures.

    Carries the wire error's ``code`` / ``request_id`` and the HTTP ``status``
    when the failure came from the platform; a subclass with a class-level
    ``remediation`` appends it to ``str(error)`` so tool-error rendering
    surfaces the next step without any special-casing.
    """

    #: Frozen wire code (class default; instances may override for codes
    #: without a dedicated subclass, e.g. ``forbidden`` / ``validation_error``).
    code: str | None = None

    #: Human next-step for the first-run funnel pair; ``None`` elsewhere.
    remediation: str | None = None

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        request_id: str | None = None,
        status: int | None = None,
    ) -> None:
        if self.remediation:
            message = f"{message} — {self.remediation}"
        super().__init__(message)
        if code is not None:
            self.code = code
        self.request_id = request_id
        self.status = status


class ConsentMissing(DelegationError):
    """No consent for (originator, agent) — first-run funnel step 1."""

    code = "consent_missing"
    remediation = "Ask the user to grant consent, then retry."


class ProviderNotConnected(DelegationError):
    """Consent exists but the originator has no usable connection — funnel step 2."""

    code = "provider_not_connected"
    remediation = "Ask the user to connect the provider, then retry."


class WindowExpired(DelegationError):
    """Exchange attempted after the processing window (anchored on
    ``minted_at``). Terminal for this message — a new request is required."""

    code = "window_expired"


class Revoked(DelegationError):
    """Consent was revoked. Stop; the user must re-grant."""

    code = "revoked"


class AudienceNotAllowed(DelegationError):
    """Connector not on the consent allowlist (or unknown/malformed id).
    Do not retry the same audience value."""

    code = "audience_not_allowed"


class CrossOrgBlocked(DelegationError):
    """Originator and agent owner are in different orgs — hard M2 block."""

    code = "cross_org_blocked"


class DelegationDenied(DelegationError):
    """Deliberately undifferentiated platform denial (kill-switch off,
    connector mode ``owner``, no envelope server-side, participant gone).
    Terminal for this message."""

    code = "delegation_denied"


class ExchangeRateLimited(DelegationError):
    """Per-(message, agent) exchange cap exhausted. Back off."""

    code = "rate_limited"


class DelegationUnavailable(DelegationError):
    """The 404 family: message unknown, never delivered to this agent, or the
    agent was not @mentioned — deliberately indistinguishable on the wire.
    Terminal for this message."""

    code = "not_found"


class NoDelegation(DelegationError):
    """Raised client-side when the current message has no delegation envelope
    (owner-invoked or non-delegated), instead of letting the exchange 404."""


_ERRORS_BY_CODE: dict[str, type[DelegationError]] = {
    "consent_missing": ConsentMissing,
    "provider_not_connected": ProviderNotConnected,
    "window_expired": WindowExpired,
    "revoked": Revoked,
    "audience_not_allowed": AudienceNotAllowed,
    "cross_org_blocked": CrossOrgBlocked,
    "delegation_denied": DelegationDenied,
    "rate_limited": ExchangeRateLimited,
    "not_found": DelegationUnavailable,
}

_ERRORS_BY_STATUS: dict[int, type[DelegationError]] = {
    404: DelegationUnavailable,
    429: ExchangeRateLimited,
}


# --- Wire models -------------------------------------------------------------


class OnBehalfOf(BaseModel):
    """Whose authority the token carries. Platform-authored; read-only."""

    model_config = ConfigDict(extra="allow", frozen=True)

    originator_uuid: str | None = None
    originator_handle: str | None = None
    agent_uuid: str | None = None
    #: The RESOLVED canonical connector id, even if the caller sent another
    #: spelling/case of the same connector.
    audience: str | None = None


class DelegatedToken(BaseModel):
    """A short-lived provider token acting as the originator.

    ``expires_at`` is the PROVIDER's expiry, not platform-bounded; ``None``
    means unknown — the resolver then never reuses the token.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    access_token: str
    token_type: str = "bearer"
    expires_at: datetime | None = None
    obo: OnBehalfOf | None = None


# --- Resolver ----------------------------------------------------------------


class CredentialResolver:
    """Resolve delegated provider tokens for the one envelope it was built on.

    One resolver per (link, envelope): the cache is therefore keyed
    (message_id, audience) by construction — a new message means a new
    envelope means a new resolver, so a token can never outlive its message's
    delegation. ``ExecutionContext.credentials`` owns that per-turn wiring.
    """

    def __init__(self, link: "BandLink", envelope: "DelegationEnvelope | None") -> None:
        self._link = link
        #: The envelope this resolver acts under; ``None`` for a non-delegated
        #: message, in which case ``token_for`` raises :class:`NoDelegation`.
        self.envelope = envelope
        self._cache: dict[str, DelegatedToken] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def token_for(self, audience: str) -> DelegatedToken:
        """Return a live delegated token for ``audience`` (an MCP connector id).

        Raises a :class:`DelegationError` subclass per the frozen taxonomy;
        :class:`NoDelegation` client-side when the current message carries no
        envelope (I3 — owner-invoked messages are never delegated).
        """
        message_id = self.envelope.message_id if self.envelope else None
        if not message_id:
            raise NoDelegation(
                "This message carries no delegation envelope (owner-invoked or "
                "non-delegated), so there are no delegated credentials to "
                "resolve. token_for() only works while handling a cross-owner "
                "message."
            )

        lock = self._locks.setdefault(audience, asyncio.Lock())
        async with lock:
            cached = self._cache.get(audience)
            if cached is not None and _is_fresh(cached):
                return cached
            token = await self._exchange(message_id, audience)
            if token.expires_at is not None:
                self._cache[audience] = token
            else:
                # Unknown expiry: no reuse, and drop any stale predecessor.
                self._cache.pop(audience, None)
            return token

    async def _exchange(self, message_id: str, audience: str) -> DelegatedToken:
        url = self._link.rest_url.rstrip("/") + EXCHANGE_PATH
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                json={"message_id": message_id, "audience": audience},
                headers={"X-API-Key": self._link.api_key},
            )

        if response.status_code == 200:
            try:
                return DelegatedToken.model_validate(response.json())
            except Exception as exc:
                raise DelegationError(
                    f"Malformed delegation token response: {exc}", status=200
                ) from exc

        raise _error_from_response(response)


def _is_fresh(token: DelegatedToken) -> bool:
    """Whether a cached token is still safely usable (expiry minus skew)."""
    expires_at = token.expires_at
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    skew = timedelta(seconds=EXPIRY_SKEW_SECONDS)
    return expires_at - skew > datetime.now(timezone.utc)


def _error_from_response(response: httpx.Response) -> DelegationError:
    """Map a non-200 exchange response to its typed error.

    Primary key is the wire ``error.code``; an unknown or absent code falls
    back by HTTP status (the auth plug's 401 has no error envelope at all).
    """
    code: str | None = None
    message: str | None = None
    request_id: str | None = None
    try:
        payload: Any = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            raw_code = error.get("code")
            code = raw_code if isinstance(raw_code, str) else None
            raw_message = error.get("message")
            message = raw_message if isinstance(raw_message, str) else None
            raw_request_id = error.get("request_id")
            request_id = raw_request_id if isinstance(raw_request_id, str) else None

    error_cls = (
        _ERRORS_BY_CODE.get(code or "")
        or _ERRORS_BY_STATUS.get(response.status_code)
        or DelegationError
    )
    return error_cls(
        message or f"Delegation exchange failed with HTTP {response.status_code}",
        code=code,
        request_id=request_id,
        status=response.status_code,
    )


__all__ = [
    "AudienceNotAllowed",
    "ConsentMissing",
    "CredentialResolver",
    "CrossOrgBlocked",
    "DelegatedToken",
    "DelegationDenied",
    "DelegationError",
    "DelegationUnavailable",
    "EXCHANGE_PATH",
    "EXPIRY_SKEW_SECONDS",
    "ExchangeRateLimited",
    "NoDelegation",
    "OnBehalfOf",
    "ProviderNotConnected",
    "Revoked",
    "WindowExpired",
]
