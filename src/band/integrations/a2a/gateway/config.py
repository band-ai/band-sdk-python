"""Configuration for the A2A Gateway adapter."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class A2AGatewayAdapterConfig:
    """Runtime policy for the A2A Gateway adapter."""

    response_timeout_s: float | None = 300.0

    def __post_init__(self) -> None:
        if self.response_timeout_s is not None and self.response_timeout_s <= 0:
            raise ValueError("response_timeout_s must be positive or None")
