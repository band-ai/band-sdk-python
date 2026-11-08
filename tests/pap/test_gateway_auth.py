"""Every gateway request authenticates once and errors exclude private diagnostics."""

from __future__ import annotations

from pathlib import Path

import pytest
from bandpap.auth import AuthError
from bandpap.discovery import AuthDiscovery
from pydantic import ValidationError

from tests.pap.gatewaykit import ISSUER, ORIGIN, GatewayHarness, message


class TestGatewayAuth:
    async def test_discovery_metadata_and_configured_public_origin(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")
        discovery = await kit.client.get("/.well-known/poppy.json")
        assert discovery.json()["organization"]["domain"] == "service.example"
        assert discovery.json()["auth"]["issuer"] == ISSUER
        assert (
            discovery.json()["agent"]["protocols"][0]["endpoint"]
            == ORIGIN + "/poppy/conversations"
        )
        assert "operations" not in discovery.json().get("extensions", {})
        metadata = await kit.client.get("/.well-known/oauth-authorization-server")
        assert metadata.json()["issuer"] == ISSUER
        access = kit.access()
        result = await kit.request(
            access,
            "POST",
            "/poppy/conversations",
            value=message(),
            headers={"Host": "untrusted.example"},
        )
        assert result.status_code == 201
        await kit.close()

    async def test_missing_duplicate_wrong_audience_and_replayed_proof_fail(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")
        access = kit.access()
        assert (
            await kit.client.post("/poppy/conversations", json=message())
        ).status_code == 401
        wrong = kit.headers(
            access, "POST", "/poppy/conversations", aud="https://other.example"
        )
        assert (
            await kit.client.post("/poppy/conversations", json=message(), headers=wrong)
        ).status_code == 401
        headers = kit.headers(access, "POST", "/poppy/conversations")
        assert (
            await kit.client.post(
                "/poppy/conversations", json=message(), headers=headers
            )
        ).status_code == 201
        assert (
            await kit.client.post(
                "/poppy/conversations", json=message(), headers=headers
            )
        ).status_code == 401
        duplicate = list(
            kit.headers(access, "POST", "/poppy/conversations").items()
        ) + [("Authorization", "DPoP another")]
        assert (
            await kit.client.post(
                "/poppy/conversations", json=message("msg_two"), headers=duplicate
            )
        ).status_code == 401
        await kit.close()

    async def test_revocation_and_private_host_errors_are_sanitized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")
        access = kit.access()
        created = await kit.request(
            access, "POST", "/poppy/conversations", value=message()
        )
        id_ = created.json()["conversation_id"]
        kit.band.revoked.add(access.session.session_id)
        assert (
            await kit.request(access, "GET", f"/poppy/conversations/{id_}/events")
        ).status_code == 401
        kit.band.revoked.clear()

        async def fail(*args, **kwargs):
            raise RuntimeError("Bearer PRIVATE_SECRET; private host implementation")

        monkeypatch.setattr(kit.band, "authorize", fail)
        result = await kit.request(access, "GET", f"/poppy/conversations/{id_}/events")
        assert result.status_code == 500 and result.json() == {"error": "server_error"}
        assert "PRIVATE_SECRET" not in result.text
        await kit.close()

    @pytest.mark.parametrize(
        "scopes", ["poppy:read poppy:write", "bad\r\nInjected: secret", 'bad"scope']
    )
    async def test_required_scope_authentication_metadata_is_complete_and_header_safe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scopes: str
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")
        access = kit.access()

        async def missing(*args, **kwargs):
            raise AuthError("insufficient_scope", scope=scopes)

        monkeypatch.setattr(kit.band, "authorize", missing)
        result = await kit.request(
            access, "POST", "/poppy/conversations", value=message()
        )
        assert result.status_code == 403
        if scopes.startswith("poppy"):
            assert (
                result.headers["www-authenticate"]
                == f'DPoP error="insufficient_scope", scope="{scopes}"'
            )
            assert result.json()["scope"] == scopes
        else:
            assert result.json() == {"error": "access_denied"}
            assert "Injected" not in str(result.headers)
        await kit.close()

    @pytest.mark.parametrize("method", ["direct", "device"])
    async def test_advertised_signin_requires_configured_issuer_endpoint(
        self, tmp_path: Path, method: str
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")
        settings = kit.gateway.config.model_dump()
        settings["auth"] = AuthDiscovery.model_validate(
            {"issuer": ISSUER, method: {"scopes": ["poppy:read"]}}
        )
        with pytest.raises(ValidationError, match="endpoint"):
            type(kit.gateway.config).model_validate(settings)
        await kit.close()
