"""PAP signing, proof verification and encrypted host-owned credential storage."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import jwt
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from bandpap.discovery import HttpsUrl, validate_https_url
from bandpap.fetch import DiscoveryFetcher
from bandpap.storage import Scope, Session, StateStore, canonical


class AuthError(ValueError):
    def __init__(
        self,
        code: str,
        *,
        nonce: str | None = None,
        scope: str | None = None,
        extension: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.nonce = nonce
        self.scope = scope
        self.extension = extension


def digest(value: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(value.encode()).digest())
        .rstrip(b"=")
        .decode()
    )


def target(url: str) -> str:
    validate_https_url(url)
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", "", "")
    )


class SigningKey:
    def __init__(self, key: ec.EllipticCurvePrivateKey, kid: str = "pap") -> None:
        if not isinstance(key.curve, ec.SECP256R1):
            raise ValueError("PAP signing key must use P-256")
        self.key = key
        self.kid = kid

    @classmethod
    def generate(cls) -> SigningKey:
        return cls(ec.generate_private_key(ec.SECP256R1()))

    @classmethod
    def restore(cls, pem: str) -> SigningKey:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError("Expected an EC private key")
        return cls(key)

    def private_pem(self) -> str:
        return self.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()

    def public_jwk(self) -> dict[str, Any]:
        return json.loads(ECAlgorithm.to_jwk(self.key.public_key()))

    def thumbprint(self) -> str:
        return digest(canonical(self.public_jwk()))

    def sign(
        self, claims: dict[str, Any], *, typ: str = "JWT", proof: bool = False
    ) -> str:
        headers = (
            {"typ": typ, "jwk": self.public_jwk()}
            if proof
            else {"typ": typ, "kid": self.kid}
        )
        return jwt.encode(claims, self.key, algorithm="ES256", headers=headers)

    def assertion(
        self,
        client: str,
        subject: str,
        audience: str,
        *,
        now: int | None = None,
        typ: str = "JWT",
        **extra: Any,
    ) -> str:
        now = int(time.time()) if now is None else now
        return self.sign(
            {
                "iss": client,
                "sub": subject,
                "aud": audience,
                "iat": now,
                "exp": now + 60,
                "jti": secrets.token_urlsafe(32),
                **extra,
            },
            typ=typ,
        )

    def proof(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        nonce: str | None = None,
        now: int | None = None,
    ) -> str:
        claims: dict[str, Any] = {
            "jti": secrets.token_urlsafe(32),
            "htm": method.upper(),
            "htu": target(url),
            "iat": int(time.time()) if now is None else now,
        }
        if token is not None:
            claims["ath"] = digest(token)
        if nonce is not None:
            claims["nonce"] = nonce
        return self.sign(claims, typ="dpop+jwt", proof=True)


def public_key(jwk: dict[str, Any]) -> Any:
    if (
        not isinstance(jwk, dict)
        or jwk.get("kty") != "EC"
        or jwk.get("crv") != "P-256"
        or any(key in jwk for key in ("d", "p", "q", "k", "oth"))
    ):
        raise AuthError("invalid_public_key")
    return jwt.PyJWK.from_dict(jwk, algorithm="ES256").key


class SecurityStore:
    def __init__(self, store: StateStore, *, encryption_key: bytes) -> None:
        self.store = store
        self.cipher = Fernet(encryption_key)
        store.db.executescript("""
        CREATE TABLE IF NOT EXISTS pap_secrets (scope TEXT, name TEXT, value BLOB, version INTEGER NOT NULL, PRIMARY KEY(scope,name));
        CREATE TABLE IF NOT EXISTS pap_replays (namespace TEXT, id TEXT, expires REAL, PRIMARY KEY(namespace,id));
        CREATE TABLE IF NOT EXISTS pap_users (tenant TEXT, client TEXT, owner TEXT, issuer TEXT, id TEXT, PRIMARY KEY(tenant,client,owner,issuer));
        """)

    def pairwise_user(
        self, *, tenant: str, client: str, owner: str, issuer: str
    ) -> str:
        with self.store.transaction():
            self.store.db.execute(
                "INSERT OR IGNORE INTO pap_users VALUES(?,?,?,?,?)",
                (tenant, client, owner, issuer, "usr_" + secrets.token_urlsafe(24)),
            )
            return self.store.db.execute(
                "SELECT id FROM pap_users WHERE tenant=? AND client=? AND owner=? AND issuer=?",
                (tenant, client, owner, issuer),
            ).fetchone()[0]

    def get(self, scope: Scope, name: str) -> tuple[int, dict[str, Any] | None]:
        row = self.store.db.execute(
            "SELECT version,value FROM pap_secrets WHERE scope=? AND name=?",
            (scope.key(), name),
        ).fetchone()
        if row is None:
            return 0, None
        return row[0], json.loads(self.cipher.decrypt(row[1])) if row[1] else None

    def put(
        self, scope: Scope, name: str, value: dict[str, Any] | None, *, expected: int
    ) -> int:
        with self.store.transaction():
            current, _ = self.get(scope, name)
            if current != expected:
                raise AuthError("credential_changed")
            encrypted = (
                self.cipher.encrypt(canonical(value).encode())
                if value is not None
                else None
            )
            self.store.db.execute(
                "INSERT INTO pap_secrets VALUES(?,?,?,?) ON CONFLICT(scope,name) DO UPDATE SET value=excluded.value,version=excluded.version",
                (scope.key(), name, encrypted, current + 1),
            )
            return current + 1

    def consume(
        self, namespace: str, identifier: str, *, expires: float, now: float
    ) -> None:
        if (
            not isinstance(identifier, str)
            or not identifier
            or len(identifier) > 1024
            or expires <= now
        ):
            raise AuthError("invalid_grant")
        with self.store.transaction():
            self.store.db.execute("DELETE FROM pap_replays WHERE expires<=?", (now,))
            try:
                self.store.db.execute(
                    "INSERT INTO pap_replays VALUES(?,?,?)",
                    (namespace, identifier, expires),
                )
            except sqlite3.IntegrityError as error:
                raise AuthError("replayed_proof") from error


def verify_signature(
    token: str, jwk: dict[str, Any], *, typ: str | None
) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
        allowed_types = {None, "JWT", "application/jwt"} if typ is None else {typ}
        if (
            header.get("typ") not in allowed_types
            or header.get("alg") != "ES256"
            or header.get("crit")
        ):
            raise AuthError("invalid_signature")
        return jwt.decode(
            token,
            public_key(jwk),
            algorithms=["ES256"],
            options={
                "verify_aud": False,
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
            },
        )
    except (jwt.PyJWTError, TypeError, KeyError, ValueError) as error:
        raise AuthError("invalid_signature") from error


def fresh(claims: dict[str, Any], now: float, *, expires: bool) -> None:
    issued = claims.get("iat")
    if type(issued) is not int or not now - 60 <= issued <= now + 5:
        raise AuthError("expired_assertion")
    if expires:
        end = claims.get("exp")
        if type(end) is not int or not now < end <= issued + 60:
            raise AuthError("expired_assertion")


def verify_proof(
    proof: str,
    *,
    method: str,
    url: str,
    security: SecurityStore,
    token: str | None = None,
    thumbprint: str | None = None,
    nonce: str | None = None,
    now: float | None = None,
) -> str:
    now = time.time() if now is None else now
    try:
        jwk = jwt.get_unverified_header(proof)["jwk"]
        claims = verify_signature(proof, jwk, typ="dpop+jwt")
        fresh(claims, now, expires=False)
        actual = digest(canonical({key: jwk[key] for key in ("crv", "kty", "x", "y")}))
        if claims.get("htm") != method.upper() or claims.get("htu") != target(url):
            raise AuthError("invalid_dpop_proof")
        if nonce is not None and claims.get("nonce") != nonce:
            raise AuthError("use_dpop_nonce", nonce=nonce)
        if token is not None and (
            thumbprint != actual
            or not hmac.compare_digest(str(claims.get("ath", "")), digest(token))
        ):
            raise AuthError("invalid_dpop_proof")
        security.consume(
            "proof:" + actual, claims["jti"], expires=claims["iat"] + 65, now=now
        )
        return actual
    except AuthError as error:
        if error.code == "use_dpop_nonce":
            raise
        raise AuthError("invalid_dpop_proof") from error
    except (jwt.PyJWTError, TypeError, KeyError, ValueError) as error:
        raise AuthError("invalid_dpop_proof") from error


class ClientMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    client_id: HttpsUrl
    client_name: str
    jwks_uri: HttpsUrl
    redirect_uris: tuple[HttpsUrl, ...] = ()
    token_endpoint_auth_method: Literal["private_key_jwt"]
    extensions: dict[str, dict[str, Any]] = Field(default_factory=dict)


async def client_keys(
    client_id: str, *, fetcher: DiscoveryFetcher | None = None
) -> tuple[ClientMetadata, list[dict[str, Any]]]:
    fetcher = fetcher or DiscoveryFetcher()
    response = await fetcher.get_json(client_id)
    metadata = ClientMetadata.model_validate(response.document)
    if metadata.client_id != client_id or response.url != client_id:
        raise AuthError("invalid_client")
    host = urlsplit(client_id).hostname
    if any(
        urlsplit(url).hostname != host
        for url in (metadata.jwks_uri, *metadata.redirect_uris)
    ):
        raise AuthError("invalid_client")
    keys = await fetcher.get_json(metadata.jwks_uri)
    if keys.url != metadata.jwks_uri:
        raise AuthError("invalid_client")
    values = keys.document.get("keys", [])
    if not values:
        raise AuthError("invalid_client")
    if not isinstance(values, list) or any(
        not isinstance(key, dict)
        or any(private in key for private in ("d", "p", "q", "k", "oth"))
        for key in values
    ):
        raise AuthError("invalid_client")
    usable = [
        key
        for key in values
        if key.get("kty") == "EC"
        and key.get("crv") == "P-256"
        and key.get("alg", "ES256") == "ES256"
        and key.get("use", "sig") == "sig"
    ]
    if not usable:
        raise AuthError("invalid_client")
    for key in usable:
        public_key(key)
    return metadata, usable


def verify_assertion(
    token: str,
    *,
    client: str,
    issuer: str,
    keys: list[dict[str, Any]],
    security: SecurityStore,
    kind: Literal["client", "session", "browser"],
    now: float | None = None,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        matches = [key for key in keys if key.get("kid") == kid]
        if len(matches) != 1:
            raise AuthError("invalid_client")
        claims = verify_signature(
            token, matches[0], typ="poppy-browser+jwt" if kind == "browser" else None
        )
        fresh(claims, now, expires=True)
        if (
            claims.get("iss") != client
            or claims.get("aud") != issuer
            or not isinstance(claims.get("sub"), str)
            or not claims["sub"]
        ):
            raise AuthError("invalid_grant")
        if kind == "client" and claims["sub"] != client:
            raise AuthError("invalid_client")
        security.consume(
            f"assertion:{client}:{kind}", claims["jti"], expires=claims["exp"], now=now
        )
        return claims
    except (jwt.PyJWTError, TypeError, KeyError) as error:
        raise AuthError("invalid_grant") from error


class Access(BaseModel):
    model_config = ConfigDict(frozen=True)
    session: Session
    scopes: frozenset[str] = frozenset()
    extensions: dict[str, str] = Field(default_factory=dict)


class JWTAuthenticator:
    """Resource-server verification with a required authoritative live-session lookup."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        keys: list[dict[str, Any]],
        security: SecurityStore,
        resolve: Callable[[dict[str, Any]], Awaitable[Access]],
    ) -> None:
        self.issuer, self.audience, self.keys = issuer, audience, keys
        self.security, self.resolve = security, resolve

    async def authenticate(
        self,
        *,
        authorization: str,
        proof: str | None,
        method: str,
        url: str,
        bearer: bool = False,
    ) -> Access:
        scheme, _, token = authorization.partition(" ")
        if scheme != ("Bearer" if bearer else "DPoP") or not token:
            raise AuthError("invalid_token")
        try:
            header = jwt.get_unverified_header(token)
            keys = [key for key in self.keys if key.get("kid") == header.get("kid")]
            if len(keys) != 1 or header.get("alg") != "ES256":
                raise AuthError("invalid_token")
            claims = jwt.decode(
                token,
                public_key(keys[0]),
                algorithms=["ES256"],
                issuer=self.issuer,
                audience=self.audience,
                options={
                    "require": [
                        "exp",
                        "iat",
                        "iss",
                        "aud",
                        "sub",
                        "client_id",
                        "session_id",
                    ],
                    "strict_aud": True,
                },
            )
            if not bearer:
                if proof is None:
                    raise AuthError("invalid_dpop_proof")
                binding = claims.get("cnf")
                if not isinstance(binding, dict) or not isinstance(
                    binding.get("jkt"), str
                ):
                    raise AuthError("invalid_dpop_proof")
                verify_proof(
                    proof,
                    method=method,
                    url=url,
                    token=token,
                    thumbprint=binding["jkt"],
                    security=self.security,
                )
            elif "cnf" in claims or proof is not None:
                raise AuthError("invalid_token")
            access = await self.resolve(claims)
            session = access.session
            if (
                session.scope.issuer != self.issuer
                or session.scope.client != claims["client_id"]
                or session.scope.user != claims["sub"]
                or session.session_id != claims["session_id"]
                or session.account != claims.get("account")
            ):
                raise AuthError("invalid_token")
            granted = frozenset(str(claims.get("scope", "")).split())
            return access.model_copy(update={"scopes": access.scopes & granted})
        except (jwt.PyJWTError, TypeError, KeyError, ValueError) as error:
            if isinstance(error, AuthError):
                raise
            raise AuthError("invalid_token") from error


class TokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    access_token: SecretStr = Field(min_length=1)
    token_type: Literal["DPoP", "Bearer"]
    expires_in: int = Field(gt=0, strict=True)
    scope: str
    session_id: str = Field(min_length=1)
    signed_in: bool = Field(strict=True)
    refresh_token: SecretStr | None = Field(default=None, min_length=1)
