"""A resource token requires live host authority and its bound proof key."""

from __future__ import annotations

import time

import jwt
import pytest
from bandpap.auth import Access, AuthError, JWTAuthenticator, SigningKey
from bandpap.storage import Session

from tests.pap.authkit import ScriptedTransport, client


class TestAuthGateway:
    @pytest.mark.parametrize("binding", [None, [], "invalid", {}, {"jkt": 123}])
    async def test_malformed_confirmation_claims_fail_as_auth_errors(
        self, binding: object
    ) -> None:
        oauth = client(ScriptedTransport())
        issuer_key, proof_key = SigningKey.generate(), SigningKey.generate()

        async def resolve(claims: dict) -> Access:
            return Access(session=Session(scope=oauth.scope, session_id="session"))

        verifier = JWTAuthenticator(
            issuer=oauth.scope.issuer,
            audience="https://company.example/conversations",
            keys=[{**issuer_key.public_jwk(), "kid": "pap"}],
            security=oauth.security,
            resolve=resolve,
        )
        now = int(time.time())
        claims = {
            "iss": oauth.scope.issuer,
            "aud": verifier.audience,
            "iat": now,
            "exp": now + 60,
            "sub": oauth.scope.user,
            "client_id": oauth.scope.client,
            "session_id": "session",
            "cnf": binding,
        }
        token = jwt.encode(
            claims, issuer_key.key, algorithm="ES256", headers={"kid": "pap"}
        )
        proof = proof_key.proof("GET", verifier.audience, token=token)
        with pytest.raises(AuthError):
            await verifier.authenticate(
                authorization="DPoP " + token,
                proof=proof,
                method="GET",
                url=verifier.audience,
            )
        oauth.security.store.close()

    async def test_live_grant_and_token_scopes_intersect(self) -> None:
        oauth = client(ScriptedTransport())
        issuer_key, proof_key = SigningKey.generate(), SigningKey.generate()
        revoked = False

        async def resolve(claims: dict) -> Access:
            if revoked:
                raise AuthError("invalid_token")
            return Access(
                session=Session(scope=oauth.scope, session_id="session"),
                scopes=frozenset({"poppy:read"}),
            )

        verifier = JWTAuthenticator(
            issuer=oauth.scope.issuer,
            audience="https://company.example/conversations",
            keys=[{**issuer_key.public_jwk(), "kid": "pap"}],
            security=oauth.security,
            resolve=resolve,
        )
        now = int(time.time())
        token = issuer_key.sign(
            {
                "iss": oauth.scope.issuer,
                "aud": verifier.audience,
                "iat": now,
                "exp": now + 60,
                "sub": oauth.scope.user,
                "client_id": oauth.scope.client,
                "session_id": "session",
                "scope": "poppy:read poppy:write",
                "cnf": {"jkt": proof_key.thumbprint()},
            }
        )
        access = await verifier.authenticate(
            authorization="DPoP " + token,
            proof=proof_key.proof("GET", verifier.audience, token=token),
            method="GET",
            url=verifier.audience,
        )
        assert access.scopes == frozenset({"poppy:read"})
        revoked = True
        with pytest.raises(AuthError):
            await verifier.authenticate(
                authorization="DPoP " + token,
                proof=proof_key.proof("GET", verifier.audience, token=token),
                method="GET",
                url=verifier.audience,
            )
        oauth.security.store.close()

    @pytest.mark.parametrize(
        "change",
        [
            {"iss": "https://wrong.example"},
            {"aud": "https://company.example/other"},
            {"aud": ["https://company.example/conversations"]},
            {"sub": "other"},
            {"client_id": "other"},
            {"session_id": "other"},
            {"account": "other"},
            {"exp": 1},
            {"cnf": {"jkt": "wrong"}},
        ],
    )
    async def test_signed_token_cannot_cross_issuer_resource_or_live_ownership(
        self, change: dict
    ) -> None:
        oauth = client(ScriptedTransport())
        issuer_key, proof_key = SigningKey.generate(), SigningKey.generate()

        async def resolve(claims: dict) -> Access:
            return Access(session=Session(scope=oauth.scope, session_id="session"))

        verifier = JWTAuthenticator(
            issuer=oauth.scope.issuer,
            audience="https://company.example/conversations",
            keys=[{**issuer_key.public_jwk(), "kid": "pap"}],
            security=oauth.security,
            resolve=resolve,
        )
        now = int(time.time())
        claims = {
            "iss": oauth.scope.issuer,
            "aud": verifier.audience,
            "iat": now,
            "exp": now + 60,
            "sub": oauth.scope.user,
            "client_id": oauth.scope.client,
            "session_id": "session",
            "cnf": {"jkt": proof_key.thumbprint()},
        } | change
        token = issuer_key.sign(claims)
        with pytest.raises(AuthError):
            await verifier.authenticate(
                authorization="DPoP " + token,
                proof=proof_key.proof("GET", verifier.audience, token=token),
                method="GET",
                url=verifier.audience,
            )
        oauth.security.store.close()

    @pytest.mark.parametrize(
        "bearer,scheme,bound",
        [(False, "Bearer", False), (True, "DPoP", True), (True, "Bearer", True)],
    )
    async def test_bearer_and_dpop_credentials_are_not_interchangeable(
        self, bearer: bool, scheme: str, bound: bool
    ) -> None:
        oauth = client(ScriptedTransport())
        issuer_key, proof_key = SigningKey.generate(), SigningKey.generate()

        async def resolve(claims: dict) -> Access:
            return Access(session=Session(scope=oauth.scope, session_id="session"))

        verifier = JWTAuthenticator(
            issuer=oauth.scope.issuer,
            audience="https://company.example/mcp",
            keys=[{**issuer_key.public_jwk(), "kid": "pap"}],
            security=oauth.security,
            resolve=resolve,
        )
        now = int(time.time())
        claims = {
            "iss": oauth.scope.issuer,
            "aud": verifier.audience,
            "iat": now,
            "exp": now + 60,
            "sub": oauth.scope.user,
            "client_id": oauth.scope.client,
            "session_id": "session",
        }
        if bound:
            claims["cnf"] = {"jkt": proof_key.thumbprint()}
        token = issuer_key.sign(claims)
        with pytest.raises(AuthError):
            await verifier.authenticate(
                authorization=scheme + " " + token,
                proof=None,
                method="POST",
                url=verifier.audience,
                bearer=bearer,
            )
        oauth.security.store.close()
