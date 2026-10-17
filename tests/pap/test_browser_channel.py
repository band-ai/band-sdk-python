"""Browser joins are protected form posts through a controlled executor."""

from __future__ import annotations

from pathlib import Path

import jwt
import pytest
from bandpap.channels import BrowserClient

from tests.pap.channelkit import Browser, connection


class TestBrowserChannel:
    async def test_assertion_binds_audience_session_user_and_short_expiry(
        self, tmp_path: Path
    ) -> None:
        oauth, transport, store = connection(tmp_path / "state.db")
        browser = Browser()
        client = BrowserClient(oauth, browser=browser)
        command = await client.command("https://orders.company.example/order/one")
        assertion = command.form()["assertion"]
        claims = jwt.decode(
            assertion,
            oauth.identity.key.public_key(),
            algorithms=["ES256"],
            audience="https://company.example/browser-session",
        )
        assert jwt.get_unverified_header(assertion)["typ"] == "poppy-browser+jwt"
        assert claims["iss"] == oauth.scope.client
        assert claims["sub"] == oauth.scope.user
        assert claims["session_id"] == "session"
        assert claims["return_to"] == "https://orders.company.example/order/one"
        assert 0 < claims["exp"] - claims["iat"] <= 60
        assert len(claims["jti"]) >= 22
        assert assertion not in command.endpoint
        assert assertion not in repr(command)
        assert assertion not in str(command.assertion)
        assert command.method == "POST" and command.top_level
        await client.join("https://company.example/orders")
        assert len(browser.commands) == 1
        assert browser.commands[0].form()["assertion"] != assertion
        assert transport.requests == []
        store.close()

    @pytest.mark.parametrize(
        "return_to",
        [
            "https://evilcompany.example/",
            "https://company.example.attacker.example/",
            "https://attacker.example/?company.example",
            "http://company.example/",
            "https://company.example@attacker.example/",
            "https://company.example/\nsecret",
            "https://company.example/#fragment",
        ],
    )
    async def test_cross_domain_or_unsafe_returns_fail_before_browser(
        self, tmp_path: Path, return_to: str
    ) -> None:
        oauth, transport, store = connection(tmp_path / "state.db")
        browser = Browser()
        client = BrowserClient(oauth, browser=browser)
        with pytest.raises(ValueError):
            await client.join(return_to)
        assert not browser.commands
        assert not transport.requests
        store.close()
