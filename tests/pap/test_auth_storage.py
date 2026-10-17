"""Encrypted credentials and replay fences survive process replacement."""

from __future__ import annotations

from pathlib import Path

import pytest
from bandpap.auth import AuthError, SecurityStore
from bandpap.storage import StateStore
from cryptography.fernet import Fernet

from tests.pap.authkit import ScriptedTransport, client


class TestAuthStorage:
    def test_credentials_are_encrypted_and_compare_and_set_is_durable(
        self, tmp_path: Path
    ) -> None:
        path, key = tmp_path / "security.db", Fernet.generate_key()
        oauth = client(ScriptedTransport(), path=path, encryption_key=key)
        version, _ = oauth.security.get(oauth.scope, "secret")
        oauth.security.put(
            oauth.scope,
            "secret",
            {"token": "sensitive-account-token"},
            expected=version,
        )
        stored = oauth.security.store.db.execute(
            "SELECT value FROM pap_secrets WHERE name='secret'"
        ).fetchone()[0]
        assert b"sensitive-account-token" not in stored
        other = SecurityStore(StateStore(path), encryption_key=key)
        assert other.get(oauth.scope, "secret")[1] == {
            "token": "sensitive-account-token"
        }
        with pytest.raises(AuthError, match="credential_changed"):
            other.put(oauth.scope, "secret", None, expected=version)
        other.store.close()
        oauth.security.store.close()

    def test_replay_and_pairwise_identity_survive_restart_without_cross_company_linkage(
        self, tmp_path: Path
    ) -> None:
        path, key = tmp_path / "security.db", Fernet.generate_key()
        first = SecurityStore(StateStore(path), encryption_key=key)
        arguments = {
            "tenant": "tenant",
            "client": "client",
            "owner": "private-owner",
            "issuer": "https://company.example",
        }
        identity = first.pairwise_user(**arguments)
        first.consume("proof:key", "proof-one", expires=100, now=1)
        first.store.close()
        second = SecurityStore(StateStore(path), encryption_key=key)
        assert second.pairwise_user(**arguments) == identity
        assert (
            second.pairwise_user(**(arguments | {"issuer": "https://other.example"}))
            != identity
        )
        assert "private-owner" not in identity
        with pytest.raises(AuthError, match="replayed"):
            second.consume("proof:key", "proof-one", expires=100, now=2)
        second.consume("proof:key", "proof-one", expires=200, now=101)
        second.store.close()
