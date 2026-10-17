"""Fence refresh races and preserve revocation recovery across disk reopen."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from bandpap.auth import AuthError, SigningKey
from bandpap.http import ProtocolError, Response
from bandpap.oauth import OAuthClient
from cryptography.fernet import Fernet
from pydantic import SecretStr

from tests.pap.authkit import (
    ScriptedTransport,
    client,
    install_refresh,
    response,
    tokens,
)


class TestOAuthLifecycle:
    async def test_independent_clients_cannot_rotate_the_same_refresh_concurrently(
        self, tmp_path: Path
    ) -> None:
        path, encryption = tmp_path / "security.db", Fernet.generate_key()
        entered, finish = asyncio.Event(), asyncio.Event()

        async def rotate() -> Response:
            entered.set()
            await finish.wait()
            return tokens(signed=True, scopes="poppy:read", refresh="rotated")

        first = client(ScriptedTransport(rotate), path=path, encryption_key=encryption)
        install_refresh(first)
        second = client(ScriptedTransport(), path=path, encryption_key=encryption)
        running = asyncio.create_task(first.token())
        await entered.wait()
        with pytest.raises(AuthError, match="credential_busy"):
            await second.token()
        finish.set()
        await running
        assert second.record()[1]["refresh"] == "rotated"
        first.security.store.close()
        second.security.store.close()

    async def test_lost_refresh_response_is_not_replayed_after_restart(
        self, tmp_path: Path
    ) -> None:
        path, encryption = tmp_path / "security.db", Fernet.generate_key()
        first = client(
            ScriptedTransport(TimeoutError()), path=path, encryption_key=encryption
        )
        install_refresh(first)
        with pytest.raises(TimeoutError):
            await first.token()
        first.security.store.close()
        peer = ScriptedTransport(tokens())
        second = client(peer, path=path, encryption_key=encryption)
        with pytest.raises(AuthError, match="access_revoked"):
            await second.token()
        assert peer.calls == []
        second.reconnect()
        await second.token()
        assert peer.calls[0]["form"]["grant_type"].endswith("jwt-bearer")
        second.security.store.close()

    async def test_failed_revocation_remains_disabled_and_retryable_after_restart(
        self, tmp_path: Path
    ) -> None:
        path, encryption = tmp_path / "security.db", Fernet.generate_key()
        first = client(
            ScriptedTransport(response(503, error="unavailable")),
            path=path,
            encryption_key=encryption,
        )
        install_refresh(first)
        with pytest.raises(ProtocolError):
            await first.revoke()
        first.security.store.close()
        peer = ScriptedTransport(response())
        second = client(peer, path=path, encryption_key=encryption)
        with pytest.raises(AuthError, match="access_revoked"):
            second.record()
        with pytest.raises(AuthError, match="credential_busy"):
            second.reconnect()
        await second.revoke()
        assert peer.calls[0]["form"]["token"] == "refresh"
        with pytest.raises(AuthError):
            await second.token()
        second.reconnect()
        assert "refresh" not in second.record()[1]
        second.security.store.close()

    async def test_expired_exchange_claim_never_reuses_refresh(self) -> None:
        oauth = client(ScriptedTransport())
        install_refresh(oauth)
        version, record = oauth.record()
        record["busy"] = {"id": "crashed", "until": time.time() - 1, "refresh": True}
        oauth.security.put(oauth.scope, oauth.name, record, expected=version)
        with pytest.raises(AuthError, match="refresh_uncertain"):
            await oauth.token()
        oauth.security.store.close()

    async def test_remote_revocation_continues_signed_out(self) -> None:
        peer = ScriptedTransport(response(400, error="invalid_grant"), tokens())
        oauth = client(peer)
        install_refresh(oauth)
        await oauth.token()
        assert not oauth.record()[1]["signed_in"]
        assert "refresh" not in oauth.record()[1]
        oauth.security.store.close()

    async def test_late_mediated_refresh_is_retained_only_for_revocation(self) -> None:
        entered, finish = asyncio.Event(), asyncio.Event()

        async def authorize() -> Response:
            entered.set()
            await finish.wait()
            return response(
                **(
                    tokens(
                        signed=True, scopes="poppy:read", refresh="new-refresh"
                    ).json()
                    | {"status": "complete"}
                )
            )

        peer = ScriptedTransport(tokens(), authorize, response())
        oauth = client(peer)
        pending = asyncio.create_task(
            oauth.begin_mediated(
                credentials={"password": SecretStr("password")},
                scopes=frozenset({"poppy:read"}),
            )
        )
        await entered.wait()
        with pytest.raises(AuthError, match="credential_busy"):
            await oauth.revoke()
        finish.set()
        with pytest.raises(AuthError, match="access_revoked"):
            await pending
        _, record = oauth.security.get(oauth.scope, oauth.name)
        assert record["disabled"]
        assert record["refresh"] == "new-refresh"
        assert record["tokens"] == {}
        await oauth.revoke()
        assert peer.calls[-1]["form"]["token"] == "new-refresh"
        oauth.security.store.close()

    async def test_independent_owners_and_connections_do_not_share_cached_tokens_or_proof_keys(
        self,
    ) -> None:
        first = client(ScriptedTransport(tokens(access="first")))
        another_owner = OAuthClient(
            discovery=first.discovery,
            scope=first.scope.model_copy(update={"user": "another-owner"}),
            connection=first.connection,
            identity=first.identity,
            security=first.security,
            transport=ScriptedTransport(tokens(access="another-owner")),
        )
        another_connection = OAuthClient(
            discovery=first.discovery,
            scope=first.scope,
            connection="another-connection",
            identity=SigningKey.generate(),
            security=first.security,
            transport=ScriptedTransport(tokens(access="another-connection")),
        )
        first_token, first_key = await first.token()
        owner_token, owner_key = await another_owner.token()
        connection_token, connection_key = await another_connection.token()
        assert [first_token, owner_token, connection_token] == [
            "first",
            "another-owner",
            "another-connection",
        ]
        assert (
            len(
                {
                    first_key.thumbprint(),
                    owner_key.thumbprint(),
                    connection_key.thumbprint(),
                }
            )
            == 3
        )
        assert (await first.token())[0] == "first"
        first.security.store.close()
