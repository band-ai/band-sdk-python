"""Configuration for the A2A Gateway adapter."""

from __future__ import annotations

from pydantic import PositiveFloat

from band.core.adapterconfig import BaseAdapterConfig


class A2AGatewayAdapterConfig(BaseAdapterConfig):
    """Settings for the A2A Gateway adapter.

    Attributes:
        gateway_url: Base URL for A2A endpoints exposed by this gateway (what
            remote clients see in agent cards). ``None`` derives
            ``http://localhost:{port}``; set it when the gateway is reachable
            at a different public address.
        port: Port for the HTTP server to listen on.
        response_timeout_s: Seconds to wait for a peer's reply before failing
            the A2A task; ``None`` waits indefinitely.
    """

    gateway_url: str | None = None
    port: int = 10000
    response_timeout_s: PositiveFloat | None = 300.0

    @property
    def public_url(self) -> str:
        """The base URL advertised in agent cards."""
        return self.gateway_url or f"http://localhost:{self.port}"
