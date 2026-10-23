"""Deterministic PAP discovery documents."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def discovery_payload() -> dict[str, Any]:
    return {
        "protocol_version": "0.1",
        "organization": {"name": "Company", "domain": "shop.example"},
        "auth": {"issuer": "https://auth.example"},
        "agent": {
            "protocols": [
                {"type": "poppy", "endpoint": "https://api.example/conversations"}
            ]
        },
    }


@pytest.fixture
def oauth_payload() -> dict[str, Any]:
    return {
        "issuer": "https://auth.example",
        "token_endpoint": "https://auth.example/token",
        "revocation_endpoint": "https://auth.example/revoke",
        "poppy_domains": ["shop.example"],
        "dpop_signing_alg_values_supported": ["ES256"],
    }
