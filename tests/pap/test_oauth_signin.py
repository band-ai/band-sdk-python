"""Bind trusted sign-in completions to their requested scopes and session."""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

import pytest
from bandpap.auth import AuthError
from bandpap.http import ProtocolError
from pydantic import SecretStr, ValidationError

from tests.pap.authkit import (
    ScriptedTransport,
    client,
    install_refresh,
    response,
    tokens,
)


class TestOAuthSignin:
    async def test_direct_state_issuer_and_scope_cannot_be_swapped(self) -> None:
        peer = ScriptedTransport(
            tokens(),
            tokens(signed=True, scopes="poppy:read poppy:write", refresh="refresh"),
        )
        oauth = client(peer)
        redirect = "https://agent.example/callback"
        link = await oauth.begin_direct(
            redirect_uri=redirect,
            allowed_redirects=(redirect,),
            scopes=frozenset({"poppy:read"}),
        )
        state = parse_qs(urlsplit(link).query)["state"][0]
        with pytest.raises(AuthError, match="invalid_authorization_response"):
            await oauth.finish_direct(
                state=state, issuer="https://wrong.example", code=SecretStr("code")
            )
        with pytest.raises(AuthError, match="invalid_scope"):
            await oauth.finish_direct(
                state=state, issuer=oauth.scope.issuer, code=SecretStr("code")
            )
        assert "refresh" not in oauth.record()[1]
        with pytest.raises(AuthError, match="invalid_authorization_response"):
            await oauth.finish_direct(
                state=state, issuer=oauth.scope.issuer, code=SecretStr("code")
            )
        oauth.security.store.close()

    @pytest.mark.parametrize(
        "link",
        [
            "http://company.example/activate",
            "https://user:pass@company.example/activate",
            "javascript:evil",
        ],
    )
    async def test_device_links_are_validated_before_display(self, link: str) -> None:
        oauth = client(
            ScriptedTransport(
                tokens(),
                response(
                    device_code="secret",
                    user_code="ABC",
                    verification_uri=link,
                    expires_in=600,
                ),
            )
        )
        with pytest.raises(ValidationError):
            await oauth.begin_device(frozenset({"poppy:read"}))
        assert "device" not in oauth.record()[1]
        oauth.security.store.close()

    async def test_device_timing_slowdown_and_completion_scope(self) -> None:
        peer = ScriptedTransport(
            tokens(),
            response(
                device_code="secret-device",
                user_code="ABC",
                verification_uri="https://company.example/activate",
                expires_in=600,
                interval=5,
            ),
            response(400, error="slow_down"),
            tokens(signed=True, scopes="poppy:write", refresh="refresh"),
        )
        oauth = client(peer)
        shown = await oauth.begin_device(frozenset({"poppy:read"}))
        assert "device_code" not in shown
        with pytest.raises(AuthError, match="poll_too_early"):
            await oauth.poll_device()
        version, record = oauth.record()
        record["device"]["next"] = time.time() - 1
        oauth.security.put(oauth.scope, oauth.name, record, expected=version)
        with pytest.raises(ProtocolError, match="slow_down"):
            await oauth.poll_device()
        assert oauth.record()[1]["device"]["interval"] == 10
        version, record = oauth.record()
        record["device"]["next"] = time.time() - 1
        oauth.security.put(oauth.scope, oauth.name, record, expected=version)
        with pytest.raises(AuthError, match="invalid_scope"):
            await oauth.poll_device()
        assert "refresh" not in oauth.record()[1]
        oauth.security.store.close()

    async def test_mediated_codes_preserve_session_and_never_return_tokens_or_credentials(
        self,
    ) -> None:
        complete = tokens(
            signed=True, scopes="poppy:read", refresh="secret-refresh"
        ).json()
        peer = ScriptedTransport(
            tokens(),
            response(
                status="code_required",
                sign_in_id="signin_one",
                code={"sent_to": "phone"},
                expires_at="2099-01-01T00:00:00Z",
            ),
            response(**(complete | {"status": "complete"})),
        )
        oauth = client(peer)
        shown = await oauth.begin_mediated(
            credentials={"password": SecretStr("secret-password")},
            scopes=frozenset({"poppy:read"}),
        )
        assert shown["status"] == "code_required"
        result = await oauth.finish_mediated(
            sign_in_id="signin_one", code=SecretStr("123456")
        )
        assert result == {"status": "complete", "scope": "poppy:read"}
        assert oauth.record()[1]["refresh"] == "secret-refresh"
        assert "mediated" not in oauth.record()[1]
        assert "secret-password" not in str(oauth.record()[1])
        oauth.security.store.close()

    @pytest.mark.parametrize("flow", ["direct", "device"])
    @pytest.mark.parametrize("signed", [False, True])
    async def test_signin_completion_requires_signed_in_refresh_credential_without_replacing_existing(
        self, flow: str, signed: bool
    ) -> None:
        initial = tokens(signed=True, scopes="poppy:read", refresh="original-refresh")
        malformed = tokens(signed=signed, scopes="poppy:read" if signed else "")
        replies = [initial]
        if flow == "device":
            replies.append(
                response(
                    device_code="device",
                    user_code="ABC",
                    verification_uri="https://company.example/activate",
                    expires_in=600,
                )
            )
        replies.append(malformed)
        oauth = client(ScriptedTransport(*replies))
        install_refresh(oauth, refresh="original-refresh")
        if flow == "direct":
            redirect = "https://agent.example/callback"
            link = await oauth.begin_direct(
                redirect_uri=redirect,
                allowed_redirects=(redirect,),
                scopes=frozenset({"poppy:read"}),
            )
            state = parse_qs(urlsplit(link).query)["state"][0]
            with pytest.raises(AuthError, match="invalid_token_response"):
                await oauth.finish_direct(
                    state=state, issuer=oauth.scope.issuer, code=SecretStr("code")
                )
        else:
            await oauth.begin_device(frozenset({"poppy:read"}))
            version, record = oauth.record()
            record["device"]["next"] = time.time() - 1
            oauth.security.put(oauth.scope, oauth.name, record, expected=version)
            with pytest.raises(AuthError, match="invalid_token_response"):
                await oauth.poll_device()
        assert oauth.record()[1]["refresh"] == "original-refresh"
        assert oauth.record()[1]["signed_in"]
        oauth.security.store.close()
