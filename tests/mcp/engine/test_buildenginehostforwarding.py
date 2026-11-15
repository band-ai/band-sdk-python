"""MCP engine behavior."""

from __future__ import annotations

from mcp.server.transport_security import TransportSecuritySettings

from band.integrations.mcp.engine import (
    EngineSpec,
    build_engine,
)


class TestBuildEngineHostForwarding:
    """``build_engine``'s ``host`` param (regression, found live via the Letta
    lane): FastMCP's own constructor auto-enables loopback-only DNS-rebinding
    protection whenever ``transport_security is None and host in
    ("127.0.0.1", "localhost", "::1")`` -- unconditionally, since a caller
    never told it otherwise, FastMCP always saw its own ``host="127.0.0.1"``
    default and took that branch even when the real caller (LocalMCPServer)
    was bound to a non-loopback host for a documented Docker-callback case,
    rejecting every real caller with a 421."""

    def test_default_host_still_gets_loopback_protection(self) -> None:
        mcp = build_engine(EngineSpec(name="test", tools=()))
        settings = mcp.settings.transport_security
        assert settings is not None
        assert settings.enable_dns_rebinding_protection is True
        assert "127.0.0.1:*" in settings.allowed_hosts

    def test_non_loopback_host_does_not_get_loopback_only_protection(self) -> None:
        mcp = build_engine(EngineSpec(name="test", tools=()), host="0.0.0.0")
        assert mcp.settings.transport_security is None

    def test_explicit_transport_security_overrides_host_auto_detection(self) -> None:

        explicit = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["host.docker.internal:*"],
        )
        mcp = build_engine(
            EngineSpec(name="test", tools=()),
            host="0.0.0.0",
            transport_security=explicit,
        )
        assert mcp.settings.transport_security == explicit
