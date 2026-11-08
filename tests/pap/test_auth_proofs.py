"""Reject forged, replayed and malformed proofs before granting authority."""

from __future__ import annotations

import time

import jwt
import pytest
from bandpap.auth import (
    AuthError,
    SecurityStore,
    SigningKey,
    client_keys,
    verify_assertion,
    verify_proof,
)
from bandpap.storage import StateStore
from cryptography.fernet import Fernet

from tests.pap.authkit import MetadataFetcher


class TestAuthProofs:
    @pytest.mark.parametrize(
        "jwk", [None, [], "invalid", {"kty": "oct", "k": "secret"}]
    )
    def test_malformed_public_key_is_an_authentication_error(self, jwk: object) -> None:
        key = SigningKey.generate()
        proof = jwt.encode(
            {
                "jti": "unique",
                "iat": int(time.time()),
                "htm": "POST",
                "htu": "https://company.example/token",
            },
            key.key,
            algorithm="ES256",
            headers={"typ": "dpop+jwt", "jwk": jwk},
        )
        security = SecurityStore(
            StateStore(":memory:"), encryption_key=Fernet.generate_key()
        )
        with pytest.raises(AuthError):
            verify_proof(
                proof,
                method="POST",
                url="https://company.example/token",
                security=security,
            )
        security.store.close()

    def test_proof_binds_token_method_target_nonce_and_replay(self) -> None:
        key = SigningKey.generate()
        security = SecurityStore(
            StateStore(":memory:"), encryption_key=Fernet.generate_key()
        )
        proof = key.proof(
            "POST",
            "https://company.example/api?query=yes",
            token="token",
            nonce="nonce",
        )
        for change in [
            {"method": "GET"},
            {"url": "https://company.example/else"},
            {"token": "other"},
            {"thumbprint": "other"},
            {"nonce": "other"},
        ]:
            arguments = {
                "method": "POST",
                "url": "https://company.example/api",
                "token": "token",
                "thumbprint": key.thumbprint(),
                "nonce": "nonce",
            } | change
            with pytest.raises(AuthError):
                verify_proof(proof, security=security, **arguments)
        assert (
            verify_proof(
                proof,
                method="POST",
                url="https://company.example/api",
                token="token",
                thumbprint=key.thumbprint(),
                nonce="nonce",
                security=security,
            )
            == key.thumbprint()
        )
        with pytest.raises(AuthError, match="invalid_dpop_proof"):
            verify_proof(
                proof,
                method="POST",
                url="https://company.example/api",
                token="token",
                thumbprint=key.thumbprint(),
                nonce="nonce",
                security=security,
            )
        security.store.close()

    def test_assertion_accepts_optional_typ_but_exact_issuer_and_replay(self) -> None:
        key = SigningKey.generate()
        security = SecurityStore(
            StateStore(":memory:"), encryption_key=Fernet.generate_key()
        )
        now = int(time.time())
        claims = {
            "iss": "https://agent.example/client",
            "sub": "https://agent.example/client",
            "aud": "https://company.example",
            "iat": now,
            "exp": now + 60,
            "jti": "random128bitsabcdefghijklmno",
        }
        token = jwt.encode(
            claims, key.key, algorithm="ES256", headers={"typ": None, "kid": "pap"}
        )
        arguments = {
            "client": claims["iss"],
            "issuer": claims["aud"],
            "keys": [{**key.public_jwk(), "kid": "pap"}],
            "security": security,
            "kind": "client",
        }
        with pytest.raises(AuthError):
            verify_assertion(
                token, **(arguments | {"issuer": "https://company.example/token"})
            )
        assert verify_assertion(token, **arguments)["sub"] == claims["sub"]
        with pytest.raises(AuthError, match="replayed"):
            verify_assertion(token, **arguments)
        security.store.close()

    async def test_unrelated_public_keys_do_not_hide_a_supported_signing_key(
        self,
    ) -> None:
        key = SigningKey.generate()
        client_id = "https://agent.example/client.json"
        supported = key.public_jwk() | {"kid": "pap"}
        fetcher = MetadataFetcher(
            {
                client_id: {
                    "client_id": client_id,
                    "client_name": "Agent",
                    "jwks_uri": "https://agent.example/keys.json",
                    "token_endpoint_auth_method": "private_key_jwt",
                },
                "https://agent.example/keys.json": {
                    "keys": [
                        {
                            "kty": "RSA",
                            "kid": "other",
                            "alg": "RS256",
                            "n": "public-modulus",
                            "e": "AQAB",
                        },
                        supported,
                    ]
                },
            }
        )
        _, keys = await client_keys(client_id, fetcher=fetcher)
        assert keys == [supported]
