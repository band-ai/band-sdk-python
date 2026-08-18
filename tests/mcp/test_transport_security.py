"""Tests for transport security configuration (INT-87).

These tests verify that band-mcp properly exposes DNS rebinding protection
settings, allowing users to configure allowed hosts for Docker/remote deployments.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from mcp.server.transport_security import (
    TransportSecurityMiddleware,
    TransportSecuritySettings,
)
from starlette.datastructures import Headers
from starlette.requests import Request


class TestTransportSecuritySettings:
    """Tests for band-mcp transport security configuration."""

    def test_default_enables_dns_rebinding_protection(self) -> None:
        """DNS rebinding protection should be enabled by default for security."""
        from band_mcp.config import Settings

        settings = Settings()

        assert settings.enable_dns_rebinding_protection is True

    def test_default_allowed_hosts_is_empty(self) -> None:
        """Allowed hosts should be empty by default (users must configure)."""
        from band_mcp.config import Settings

        settings = Settings()

        assert settings.allowed_hosts == []

    def test_default_allowed_origins_is_empty(self) -> None:
        """Allowed origins should be empty by default."""
        from band_mcp.config import Settings

        settings = Settings()

        assert settings.allowed_origins == []

    def test_can_disable_protection_via_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Users should be able to disable protection via environment variable."""
        monkeypatch.setenv("ENABLE_DNS_REBINDING_PROTECTION", "false")

        from band_mcp.config import Settings

        settings = Settings()

        assert settings.enable_dns_rebinding_protection is False

    def test_can_configure_allowed_hosts_via_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Users should be able to configure allowed hosts via environment variable."""
        monkeypatch.setenv("ALLOWED_HOSTS", '["localhost:*", "host.docker.internal:*"]')

        from band_mcp.config import Settings

        settings = Settings()

        assert settings.allowed_hosts == ["localhost:*", "host.docker.internal:*"]

    def test_can_configure_allowed_origins_via_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Users should be able to configure allowed origins via environment variable."""
        monkeypatch.setenv("ALLOWED_ORIGINS", '["http://localhost:3000"]')

        from band_mcp.config import Settings

        settings = Settings()

        assert settings.allowed_origins == ["http://localhost:3000"]


class TestMcpTransportSecurityIntegration:
    """Tests that FastMCP instance is configured with transport security."""

    def test_mcp_has_transport_security_configured(self) -> None:
        """The FastMCP instance should have transport_security settings."""
        from band_mcp.shared import mcp

        assert mcp.settings.transport_security is not None

    def test_mcp_transport_security_reflects_settings(self) -> None:
        """Transport security should reflect the configured settings."""
        from band_mcp.config import settings
        from band_mcp.shared import mcp

        transport_security = mcp.settings.transport_security

        assert (
            transport_security.enable_dns_rebinding_protection
            == settings.enable_dns_rebinding_protection
        )
        assert transport_security.allowed_hosts == settings.allowed_hosts
        assert transport_security.allowed_origins == settings.allowed_origins


class TestDnsRebindingProtectionBehavior:
    """Tests demonstrating DNS rebinding protection behavior.

    These tests verify the MCP SDK middleware behavior to ensure our
    configuration is applied correctly.
    """

    def test_empty_allowed_hosts_blocks_all_requests(self) -> None:
        """When allowed_hosts is empty, all hosts are blocked."""
        middleware = TransportSecurityMiddleware(
            TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[],
            )
        )

        # All hosts should be blocked
        assert middleware._validate_host("localhost:8000") is False
        assert middleware._validate_host("127.0.0.1:8000") is False
        assert middleware._validate_host("host.docker.internal:8000") is False

    def test_wildcard_port_matching(self) -> None:
        """Wildcard port patterns (host:*) should match any port."""
        middleware = TransportSecurityMiddleware(
            TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=["localhost:*", "host.docker.internal:*"],
            )
        )

        # Wildcard should match any port
        assert middleware._validate_host("localhost:8000") is True
        assert middleware._validate_host("localhost:3000") is True
        assert middleware._validate_host("host.docker.internal:8002") is True

        # Non-matching host should be blocked
        assert middleware._validate_host("evil.com:8000") is False

    def test_exact_host_port_matching(self) -> None:
        """Exact host:port entries should only match that specific combination."""
        middleware = TransportSecurityMiddleware(
            TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=["localhost:8000"],
            )
        )

        # Exact match works
        assert middleware._validate_host("localhost:8000") is True

        # Different port does not match
        assert middleware._validate_host("localhost:9000") is False

    def test_disabled_protection_allows_all(self) -> None:
        """When protection is disabled, validation is skipped."""
        middleware = TransportSecurityMiddleware(
            TransportSecuritySettings(enable_dns_rebinding_protection=False)
        )

        assert middleware.settings.enable_dns_rebinding_protection is False

    @pytest.fixture
    def mock_request_factory(self) -> Callable[[str], Request]:
        """Factory to create mock Starlette requests with custom Host header."""

        def _create(host: str) -> Request:
            request = MagicMock(spec=Request)
            request.headers = Headers({"host": host})
            return request

        return _create

    @pytest.mark.asyncio
    async def test_blocked_request_returns_421(
        self, mock_request_factory: Callable[[str], Request]
    ) -> None:
        """Blocked requests should return 421 Misdirected Request."""
        middleware = TransportSecurityMiddleware(
            TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=["localhost:*"],
            )
        )

        request = mock_request_factory("host.docker.internal:8000")
        response = await middleware.validate_request(request)

        assert response is not None
        assert response.status_code == 421

    @pytest.mark.asyncio
    async def test_allowed_request_returns_none(
        self, mock_request_factory: Callable[[str], Request]
    ) -> None:
        """Allowed requests should return None (pass validation)."""
        middleware = TransportSecurityMiddleware(
            TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=["localhost:*", "host.docker.internal:*"],
            )
        )

        request = mock_request_factory("host.docker.internal:8000")
        response = await middleware.validate_request(request)

        assert response is None
