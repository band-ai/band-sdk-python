"""Personal-agent OAuth lifecycle with durable credential fencing."""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import unquote, urlencode, urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter

from bandpap.auth import AuthError, SecurityStore, SigningKey, TokenResponse, digest
from bandpap.discovery import HttpsUrl, validate_https_url
from bandpap.fetch import VerifiedDiscovery
from bandpap.http import ProtocolError, Response, Transport
from bandpap.storage import Scope
from bandpap.wire import Timestamp

JWT_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
CLIENT_ASSERTION = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
PROTECTED_HEADERS = frozenset(
    {
        "authorization",
        "dpop",
        "host",
        "cookie",
        "proxy-authorization",
        "content-length",
        "transfer-encoding",
        "content-type",
        "accept-encoding",
    }
)


def within(url: str, endpoint: str) -> bool:
    try:
        validate_https_url(url)
        validate_https_url(endpoint)
    except ValueError:
        return False
    candidate, allowed = urlsplit(url), urlsplit(endpoint)
    path = candidate.path
    # Reject encoded separators and traversal before an HTTP server can normalize them.
    for _ in range(4):
        decoded = unquote(path)
        if "\\" in decoded or any(part in {".", ".."} for part in decoded.split("/")):
            return False
        if decoded == path:
            break
        if decoded.count("/") != path.count("/"):
            return False
        path = decoded
    else:
        return False
    return (
        candidate.scheme == allowed.scheme == "https"
        and candidate.netloc.lower() == allowed.netloc.lower()
        and (
            candidate.path == allowed.path
            or candidate.path.startswith(allowed.path.rstrip("/") + "/")
        )
    )


class DeviceAuthorization(BaseModel):
    model_config = ConfigDict(extra="ignore")
    device_code: SecretStr = Field(min_length=1)
    user_code: str = Field(min_length=1)
    verification_uri: HttpsUrl
    verification_uri_complete: HttpsUrl | None = None
    expires_in: int = Field(strict=True, gt=0)
    interval: int = Field(default=5, strict=True, gt=0)


class OAuthClient:
    """Host-owned client. Interrupted refreshes require explicit reconnect, never replay."""

    def __init__(
        self,
        *,
        discovery: VerifiedDiscovery,
        scope: Scope,
        connection: str,
        identity: SigningKey,
        security: SecurityStore,
        transport: Transport | None = None,
    ) -> None:
        if discovery.metadata is None or scope.issuer != discovery.metadata.issuer:
            raise AuthError("invalid_issuer")
        self.discovery, self.scope, self.connection = discovery, scope, connection
        self.identity, self.security = identity, security
        self.transport = transport or Transport()
        self.lock = asyncio.Lock()
        self.name = "oauth:" + connection
        version, record = security.get(scope, self.name)
        if record is None and version == 0:
            try:
                security.put(scope, self.name, self.new_record(), expected=version)
            except AuthError as error:
                if error.code != "credential_changed":
                    raise
        self.nonces: dict[str, str] = {}

    @staticmethod
    def new_record() -> dict[str, Any]:
        return {
            "key": SigningKey.generate().private_pem(),
            "tokens": {},
            "disabled": False,
        }

    def record(self) -> tuple[int, dict[str, Any]]:
        version, record = self.security.get(self.scope, self.name)
        if record is None or record.get("disabled"):
            raise AuthError("access_revoked")
        return version, record

    def reconnect(self) -> None:
        """Host explicitly starts signed out after resolving revocation/refresh uncertainty."""
        version, record = self.security.get(self.scope, self.name)
        if record and (record.get("revoke_pending") or record.get("busy")):
            raise AuthError("credential_busy")
        self.security.put(self.scope, self.name, self.new_record(), expected=version)

    def client_form(self) -> dict[str, str]:
        return {
            "client_id": self.scope.client,
            "client_assertion_type": CLIENT_ASSERTION,
            "client_assertion": self.identity.assertion(
                self.scope.client, self.scope.client, self.scope.issuer
            ),
        }

    def claim(self, *, refresh: bool) -> tuple[int, dict[str, Any], str]:
        version, record = self.record()
        now = time.time()
        if record.get("retry_until", 0) > now:
            raise AuthError("rate_limited")
        busy = record.get("busy")
        if busy:
            if busy["until"] > now:
                raise AuthError("credential_busy")
            if busy["refresh"]:
                record["disabled"] = True
                record["uncertain_refresh"] = True
                record.pop("busy")
                record["tokens"] = {}
                self.security.put(self.scope, self.name, record, expected=version)
                raise AuthError("refresh_uncertain")
        claim = secrets.token_urlsafe(24)
        record["busy"] = {"id": claim, "until": now + 180, "refresh": refresh}
        version = self.security.put(self.scope, self.name, record, expected=version)
        return version, record, claim

    def release(
        self,
        version: int,
        record: dict[str, Any],
        claim: str,
        *,
        uncertain: bool = False,
    ) -> None:
        current_version, current = self.security.get(self.scope, self.name)
        if current is None or current.get("busy", {}).get("id") != claim:
            raise AuthError("credential_changed")
        if current_version != version:
            # A concurrent revocation fences ordinary use but retains rotated revocation material.
            if current.get("disabled") and current.get("revoke_pending"):
                if record.get("refresh"):
                    current["refresh"] = record["refresh"]
                current.pop("busy", None)
                self.security.put(
                    self.scope, self.name, current, expected=current_version
                )
                raise AuthError("access_revoked")
            raise AuthError("credential_changed")
        record.pop("busy", None)
        if uncertain:
            record["disabled"] = True
            record["uncertain_refresh"] = True
            record["tokens"] = {}
        self.security.put(self.scope, self.name, record, expected=version)

    async def exchange(
        self,
        fields: dict[str, str],
        *,
        bearer: bool = False,
        allowed_scopes: frozenset[str] | None = None,
    ) -> TokenResponse:
        refreshing = fields.get("grant_type") == "refresh_token"
        version, record, claim = self.claim(refresh=refreshing)
        fields = dict(fields)
        if refreshing:
            fields["refresh_token"] = record["refresh"]
            if "session_id" in fields:
                fields["session_id"] = record["session"]
            if allowed_scopes is None and "refresh_scopes" in record:
                allowed_scopes = frozenset(record["refresh_scopes"])
        metadata = self.discovery.metadata
        assert metadata is not None
        key = SigningKey.restore(record["key"])
        try:
            result = None
            for attempt in range(2):
                form = {**fields, **self.client_form()}
                if form.get("grant_type") == JWT_GRANT:
                    form["assertion"] = self.identity.assertion(
                        self.scope.client, self.scope.user, self.scope.issuer
                    )
                headers = (
                    {}
                    if bearer
                    else {
                        "DPoP": key.proof(
                            "POST",
                            metadata.token_endpoint,
                            nonce=self.nonces.get(metadata.token_endpoint),
                        )
                    }
                )
                result = await self.transport.request(
                    "POST", metadata.token_endpoint, headers=headers, form=form
                )
                nonce = result.headers.get("dpop-nonce")
                if nonce:
                    self.nonces[metadata.token_endpoint] = nonce
                if (
                    result.status == 400
                    and nonce
                    and attempt == 0
                    and result.json().get("error") == "use_dpop_nonce"
                ):
                    continue
                break
            assert result is not None
            if result.status != 200:
                error = ProtocolError(result)
                if result.status == 429:
                    record["retry_until"] = time.time() + result.retry_delay(
                        time.time()
                    )
                if refreshing and error.code == "invalid_grant":
                    record.pop("refresh", None)
                    record["tokens"] = {}
                    record["signed_in"] = False
                self.release(version, record, claim)
                raise error
            token = TokenResponse.model_validate(result.json())
            if fields.get("grant_type") in {"authorization_code", DEVICE_GRANT} and (
                not token.signed_in or token.refresh_token is None
            ):
                raise AuthError("invalid_token_response")
            if token.token_type != ("Bearer" if bearer else "DPoP") or (
                fields.get("session_id") and token.session_id != fields["session_id"]
            ):
                raise AuthError("invalid_token_response")
            if (
                not token.signed_in and (token.scope or token.refresh_token is not None)
            ) or (fields.get("grant_type") == JWT_GRANT and token.signed_in):
                raise AuthError("invalid_token_response")
            permitted = allowed_scopes
            if permitted is None and "scope" in fields:
                permitted = frozenset(fields["scope"].split())
            if permitted is not None and not set(token.scope.split()) <= permitted:
                raise AuthError("invalid_scope")
            if token.refresh_token is not None:
                record["refresh"] = token.refresh_token.get_secret_value()
                record["refresh_scopes"] = sorted(token.scope.split())
            if token.signed_in != record.get("signed_in", False):
                record["tokens"] = {}
            record["session"] = token.session_id
            record["signed_in"] = token.signed_in
            name = ("Bearer:" if bearer else "DPoP:") + fields.get("resource", "")
            record["tokens"][name] = {
                "access": token.access_token.get_secret_value(),
                "expires": time.time() + token.expires_in,
                "scope": token.scope,
            }
            self.release(version, record, claim)
            return token
        except ProtocolError:
            raise
        except BaseException:
            current_version, current = self.security.get(self.scope, self.name)
            if current is not None and current.get("busy", {}).get("id") == claim:
                self.release(current_version, current, claim, uncertain=refreshing)
            raise

    async def token(
        self, *, resource: str | None = None, bearer: bool = False, force: bool = False
    ) -> tuple[str, SigningKey]:
        if bearer and not any(
            api.type == "mcp" and api.url == resource
            for api in self.discovery.document.apis
        ):
            raise AuthError("invalid_resource")
        async with self.lock:
            _, record = self.record()
            name = ("Bearer:" if bearer else "DPoP:") + (resource or "")
            cached = record["tokens"].get(name)
            if not force and cached and cached["expires"] > time.time() + 10:
                return cached["access"], SigningKey.restore(record["key"])
            for attempt in range(2):
                _, record = self.record()
                fields = (
                    {"grant_type": "refresh_token", "refresh_token": record["refresh"]}
                    if record.get("refresh")
                    else {"grant_type": JWT_GRANT}
                )
                if record.get("session"):
                    fields["session_id"] = record["session"]
                if resource is not None:
                    fields["resource"] = resource
                try:
                    result = await self.exchange(fields, bearer=bearer)
                    return result.access_token.get_secret_value(), SigningKey.restore(
                        record["key"]
                    )
                except ProtocolError as error:
                    if attempt or error.code not in {
                        "invalid_session",
                        "invalid_grant",
                    }:
                        raise
                    if error.code == "invalid_session":
                        version, current = self.record()
                        current.pop("session", None)
                        current["tokens"] = {}
                        self.security.put(
                            self.scope, self.name, current, expected=version
                        )
            raise AuthError("invalid_token_response")

    async def request(
        self,
        method: str,
        url: str,
        *,
        resource: str | None = None,
        bearer: bool = False,
        document: dict[str, Any] | None = None,
        extra_endpoints: tuple[str, ...] = (),
        headers: dict[str, str] | None = None,
        authorize: Callable[[], Awaitable[None]] | None = None,
    ) -> Response:
        extra = headers or {}
        if any(
            name.lower() in PROTECTED_HEADERS
            or re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) is None
            or any(ord(char) < 32 or ord(char) == 127 for char in name + value)
            for name, value in extra.items()
        ):
            raise AuthError("invalid_headers")
        discovery = self.discovery.document
        endpoints = (
            tuple(item.endpoint for item in discovery.agent.protocols)
            if discovery.agent
            else ()
        )
        if discovery.auth and discovery.auth.mediated:
            endpoints += (discovery.auth.mediated.endpoint,)
        operation = discovery.extensions.get("operations", {})
        if operation.get("version") == "1" and isinstance(
            operation.get("endpoint"), str
        ):
            endpoints += (operation["endpoint"],)
        if bearer:
            if url != resource or not any(
                api.type == "mcp" and api.url == url for api in discovery.apis
            ):
                raise AuthError("invalid_resource")
        elif not any(
            within(url, endpoint) for endpoint in (*endpoints, *extra_endpoints)
        ):
            raise AuthError("invalid_destination")
        _, state = self.record()
        refusal = state.get("request_retry", {}).get(url, 0)
        if refusal > time.time():
            raise AuthError("rate_limited")
        renewed = False
        for attempt in range(3):
            token, key = await self.token(
                resource=resource, bearer=bearer, force=renewed
            )
            renewed = False
            outgoing = {
                **extra,
                "Authorization": ("Bearer " if bearer else "DPoP ") + token,
            }
            if not bearer:
                outgoing["DPoP"] = key.proof(
                    method,
                    url,
                    token=token,
                    nonce=self.nonces.get(urlsplit(url).netloc),
                )
            if authorize is not None:
                await authorize()
            result = await self.transport.request(
                method, url, headers=outgoing, document=document
            )
            nonce = result.headers.get("dpop-nonce")
            if nonce:
                self.nonces[urlsplit(url).netloc] = nonce
            error = ProtocolError(result)
            if result.status == 429:
                version, state = self.record()
                state.setdefault("request_retry", {})[url] = (
                    time.time() + result.retry_delay(time.time())
                )
                self.security.put(self.scope, self.name, state, expected=version)
            if (
                result.status == 401
                and nonce
                and error.code == "use_dpop_nonce"
                and attempt == 0
            ):
                continue
            if result.status == 401 and error.code == "invalid_token" and attempt < 2:
                renewed = True
                continue
            if (
                result.status == 403
                and error.code == "sign_in_required"
                and attempt == 0
                and self.record()[1].get("refresh")
            ):
                renewed = True
                continue
            return result
        raise AuthError("invalid_token")

    def signin_scopes(self, method: str, scopes: frozenset[str]) -> None:
        auth = self.discovery.document.auth
        offered = getattr(auth, method, None)
        if offered is None or not scopes or not scopes <= set(offered.scopes):
            raise AuthError("invalid_scope")

    async def begin_direct(
        self,
        *,
        redirect_uri: str,
        allowed_redirects: tuple[str, ...],
        scopes: frozenset[str],
    ) -> str:
        self.signin_scopes("direct", scopes)
        metadata = self.discovery.metadata
        assert metadata is not None
        validate_https_url(redirect_uri)
        if (
            metadata.authorization_endpoint is None
            or redirect_uri not in allowed_redirects
            or urlsplit(redirect_uri).hostname != urlsplit(self.scope.client).hostname
        ):
            raise AuthError("invalid_scope_or_redirect")
        await self.token()
        version, record = self.record()
        if record.get("busy"):
            raise AuthError("credential_busy")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        record["direct"] = {
            "state": state,
            "verifier": verifier,
            "redirect": redirect_uri,
            "scopes": sorted(scopes),
            "expires": time.time() + 600,
            "session": record["session"],
        }
        self.security.put(self.scope, self.name, record, expected=version)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.scope.client,
                "redirect_uri": redirect_uri,
                "scope": " ".join(sorted(scopes)),
                "state": state,
                "code_challenge": digest(verifier),
                "code_challenge_method": "S256",
            }
        )
        return (
            metadata.authorization_endpoint
            + ("&" if "?" in metadata.authorization_endpoint else "?")
            + query
        )

    async def finish_direct(
        self, *, state: str, issuer: str, code: SecretStr
    ) -> TokenResponse:
        version, record = self.record()
        pending = record.get("direct")
        if (
            not pending
            or pending["state"] != state
            or issuer != self.scope.issuer
            or pending["expires"] <= time.time()
            or pending["session"] != record.get("session")
        ):
            raise AuthError("invalid_authorization_response")
        record.pop("direct")
        self.security.put(self.scope, self.name, record, expected=version)
        return await self.exchange(
            {
                "grant_type": "authorization_code",
                "code": code.get_secret_value(),
                "code_verifier": pending["verifier"],
                "redirect_uri": pending["redirect"],
                "session_id": pending["session"],
            },
            allowed_scopes=frozenset(pending["scopes"]),
        )

    async def begin_device(self, scopes: frozenset[str]) -> dict[str, Any]:
        self.signin_scopes("device", scopes)
        metadata = self.discovery.metadata
        assert metadata is not None
        if metadata.device_authorization_endpoint is None:
            raise AuthError("invalid_scope")
        await self.token()
        version, record = self.record()
        response = await self.transport.request(
            "POST",
            metadata.device_authorization_endpoint,
            form={**self.client_form(), "scope": " ".join(sorted(scopes))},
        )
        if response.status != 200:
            raise ProtocolError(response)
        value = DeviceAuthorization.model_validate(response.json())
        record["device"] = {
            "code": value.device_code.get_secret_value(),
            "expires": time.time() + value.expires_in,
            "interval": value.interval,
            "next": time.time() + value.interval,
            "session": record["session"],
            "scopes": sorted(scopes),
        }
        self.security.put(self.scope, self.name, record, expected=version)
        return value.model_dump(mode="json", exclude={"device_code"}, exclude_none=True)

    async def poll_device(self) -> TokenResponse:
        version, record = self.record()
        pending = record.get("device")
        if (
            not pending
            or pending["expires"] <= time.time()
            or pending["session"] != record.get("session")
        ):
            raise AuthError("expired_token")
        if pending["next"] > time.time():
            raise AuthError("poll_too_early")
        pending["next"] = time.time() + pending["interval"]
        self.security.put(self.scope, self.name, record, expected=version)
        try:
            result = await self.exchange(
                {
                    "grant_type": DEVICE_GRANT,
                    "device_code": pending["code"],
                    "session_id": pending["session"],
                },
                allowed_scopes=frozenset(pending["scopes"]),
            )
        except ProtocolError as error:
            version, record = self.record()
            if error.code == "slow_down":
                record["device"]["interval"] += 5
                record["device"]["next"] = time.time() + record["device"]["interval"]
            elif error.code != "authorization_pending":
                record.pop("device", None)
            self.security.put(self.scope, self.name, record, expected=version)
            raise
        version, record = self.record()
        record.pop("device", None)
        self.security.put(self.scope, self.name, record, expected=version)
        return result

    async def begin_mediated(
        self, *, credentials: dict[str, SecretStr], scopes: frozenset[str]
    ) -> dict[str, Any]:
        self.signin_scopes("mediated", scopes)
        auth = self.discovery.document.auth
        assert auth is not None and auth.mediated is not None
        if set(credentials) != {field.name for field in auth.mediated.fields}:
            raise AuthError("invalid_credentials")
        return await self.mediated_exchange(
            auth.mediated.endpoint,
            scopes=scopes,
            document={
                "scope": " ".join(sorted(scopes)),
                "credentials": {
                    key: value.get_secret_value() for key, value in credentials.items()
                },
            },
        )

    async def finish_mediated(
        self, *, sign_in_id: str, code: SecretStr
    ) -> dict[str, Any]:
        _, record = self.record()
        pending = record.get("mediated")
        if (
            not pending
            or pending["id"] != sign_in_id
            or pending["session"] != record.get("session")
            or pending["expires"] <= time.time()
        ):
            raise AuthError("sign_in_not_found")
        auth = self.discovery.document.auth
        assert auth is not None and auth.mediated is not None
        return await self.mediated_exchange(
            auth.mediated.endpoint + "/" + sign_in_id,
            scopes=frozenset(pending["scopes"]),
            document={"code": code.get_secret_value()},
        )

    async def mediated_exchange(
        self, url: str, *, scopes: frozenset[str], document: dict[str, Any]
    ) -> dict[str, Any]:
        await self.token()
        version, record, claim = self.claim(refresh=False)
        try:
            response = await self.request("POST", url, document=document)
            public = self.mediated_result(response, scopes=scopes, record=record)
            self.release(version, record, claim)
            return public
        except BaseException:
            current_version, current = self.security.get(self.scope, self.name)
            if current is not None and current.get("busy", {}).get("id") == claim:
                self.release(current_version, current, claim)
            raise

    def mediated_result(
        self, response: Response, *, scopes: frozenset[str], record: dict[str, Any]
    ) -> dict[str, Any]:
        if response.status != 200:
            raise ProtocolError(response)
        value = response.json()
        status = value.get("status")
        if status == "code_required":
            from_value = value.get("expires_at")
            expiry = TypeAdapter(Timestamp).validate_python(from_value).timestamp()
            identifier = value.get("sign_in_id")
            if (
                not isinstance(identifier, str)
                or not identifier
                or "/" in identifier
                or ".." in identifier
            ):
                raise AuthError("invalid_sign_in_response")
            record["mediated"] = {
                "id": identifier,
                "expires": expiry,
                "session": record["session"],
                "scopes": sorted(scopes),
            }
            public = {
                key: value[key]
                for key in ("status", "sign_in_id", "expires_at")
                if key in value
            }
            hint = value.get("code")
            if not isinstance(hint, dict) or not isinstance(hint.get("sent_to"), str):
                raise AuthError("invalid_sign_in_response")
            public["code"] = {"sent_to": hint["sent_to"]}
        elif status == "complete":
            token = TokenResponse.model_validate(value)
            if (
                token.token_type != "DPoP"
                or not token.signed_in
                or token.session_id != record["session"]
                or not set(token.scope.split()) <= scopes
                or token.refresh_token is None
            ):
                raise AuthError("invalid_token_response")
            record["refresh"] = token.refresh_token.get_secret_value()
            record["refresh_scopes"] = sorted(token.scope.split())
            record["signed_in"] = True
            record["tokens"] = {
                "DPoP:": {
                    "access": token.access_token.get_secret_value(),
                    "expires": time.time() + token.expires_in,
                    "scope": token.scope,
                }
            }
            record.pop("mediated", None)
            public = {"status": "complete", "scope": token.scope}
        elif status in {"failed", "expired"}:
            record.pop("mediated", None)
            public = {"status": status}
        else:
            raise AuthError("invalid_sign_in_response")
        return public

    async def revoke(self) -> None:
        version, record = self.security.get(self.scope, self.name)
        if record is None:
            raise AuthError("access_revoked")
        record["disabled"] = True
        record["tokens"] = {}
        record["revoke_pending"] = True
        for field in ("direct", "device", "mediated"):
            record.pop(field, None)
        version = self.security.put(self.scope, self.name, record, expected=version)
        busy = record.get("busy")
        if busy and busy["until"] > time.time():
            raise AuthError("credential_busy")
        record.pop("busy", None)
        metadata = self.discovery.metadata
        assert metadata is not None
        if record.get("refresh"):
            response = await self.transport.request(
                "POST",
                metadata.revocation_endpoint,
                form={
                    **self.client_form(),
                    "token": record["refresh"],
                    "token_type_hint": "refresh_token",
                },
            )
            if response.status != 200:
                raise ProtocolError(response)
        record.pop("refresh", None)
        record.pop("revoke_pending", None)
        self.security.put(self.scope, self.name, record, expected=version)
