"""Keep authenticated requests destination-bound and renew only within bounds."""

from __future__ import annotations

import pytest
from bandpap.auth import AuthError
from bandpap.http import Response
from bandpap.oauth import within

from tests.pap.authkit import ScriptedTransport, client, response, tokens


class TestOAuthRequests:
    @pytest.mark.parametrize(
        "path",
        [
            "/conversations/../private",
            "/conversations/%2e%2e/private",
            "/conversations/%252e%252e/private",
            "/conversations/id%2f..%2fprivate",
        ],
    )
    def test_path_traversal_is_not_an_authorized_endpoint(self, path: str) -> None:
        assert not within(
            "https://company.example" + path, "https://company.example/conversations"
        )

    @pytest.mark.parametrize(
        "name",
        ["Authorization", "authorization", "DPoP", "Host", "Cookie", "Content-Length"],
    )
    async def test_external_headers_cannot_override_credentials_or_framing(
        self, name: str
    ) -> None:
        peer = ScriptedTransport()
        oauth = client(peer)
        with pytest.raises(AuthError, match="invalid_headers"):
            await oauth.request(
                "GET",
                "https://company.example/conversations/c/events",
                headers={name: "forged"},
            )
        assert peer.calls == []
        oauth.security.store.close()

    async def test_invalid_token_renews_and_mcp_headers_remain_available(self) -> None:
        peer = ScriptedTransport(
            tokens(access="old"),
            Response(401, {"www-authenticate": 'DPoP error="invalid_token"'}),
            tokens(access="new"),
            response(conversation_id="c", status="idle", responder="agent"),
        )
        oauth = client(peer)
        result = await oauth.request(
            "GET",
            "https://company.example/conversations/c/events",
            headers={"MCP-Protocol-Version": "2025-06-18"},
        )
        assert result.status == 200
        assert peer.calls[1]["headers"]["Authorization"] == "DPoP old"
        assert peer.calls[3]["headers"]["Authorization"] == "DPoP new"
        assert peer.calls[3]["headers"]["MCP-Protocol-Version"] == "2025-06-18"
        oauth.security.store.close()

    async def test_nonce_challenge_gets_one_fresh_proof(self) -> None:
        peer = ScriptedTransport(
            tokens(),
            Response(
                401,
                {
                    "www-authenticate": 'DPoP error="use_dpop_nonce"',
                    "dpop-nonce": "nonce",
                },
            ),
            response(),
        )
        oauth = client(peer)
        await oauth.request("GET", "https://company.example/conversations/c/events")
        assert peer.calls[1]["headers"]["DPoP"] != peer.calls[2]["headers"]["DPoP"]
        oauth.security.store.close()

    async def test_rate_limit_deadline_fences_immediate_retries(self) -> None:
        peer = ScriptedTransport(
            tokens(), Response(429, {"retry-after": "60"}, b'{"error":"rate_limited"}')
        )
        oauth = client(peer)
        assert (
            await oauth.request("GET", "https://company.example/conversations/c/events")
        ).status == 429
        with pytest.raises(AuthError, match="rate_limited"):
            await oauth.request("GET", "https://company.example/conversations/c/events")
        assert len(peer.calls) == 2
        oauth.security.store.close()

    @pytest.mark.parametrize(
        "resource,url",
        [
            ("https://attacker.example/mcp", "https://attacker.example/mcp"),
            ("https://company.example/mcp", "https://company.example/mcp/child"),
            (None, "https://company.example/mcp"),
        ],
    )
    async def test_mcp_requires_exact_advertised_resource_and_endpoint(
        self, resource: str | None, url: str
    ) -> None:
        peer = ScriptedTransport()
        oauth = client(peer)
        with pytest.raises(AuthError, match="invalid_resource"):
            await oauth.request("POST", url, resource=resource, bearer=True)
        assert not peer.calls
        oauth.security.store.close()

    async def test_authority_is_rechecked_after_token_wait_before_resource_dispatch(
        self,
    ) -> None:
        revoked = False

        async def issue_token() -> Response:
            nonlocal revoked
            revoked = True
            return tokens()

        async def authorize() -> None:
            if revoked:
                raise AuthError("access_revoked")

        peer = ScriptedTransport(issue_token)
        oauth = client(peer)
        with pytest.raises(AuthError, match="access_revoked"):
            await oauth.request(
                "POST",
                "https://company.example/conversations",
                document={"message": "private"},
                authorize=authorize,
            )
        assert len(peer.calls) == 1
        assert peer.calls[0]["url"] == "https://company.example/token"
        oauth.security.store.close()
