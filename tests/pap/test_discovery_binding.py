"""Prevent untrusted discovery from claiming another company's issuer."""

from __future__ import annotations

from typing import Any

import pytest
from bandpap.discovery import (
    DiscoveryDocument,
    OAuthMetadata,
    verify_discovery_binding,
)


class TestDiscoveryBinding:
    def test_final_redirect_host_is_the_company_domain(
        self, discovery_payload: dict[str, Any], oauth_payload: dict[str, Any]
    ) -> None:
        discovery_payload["organization"]["domain"] = "SHOP.example"
        oauth_payload["poppy_domains"] = ["shop.EXAMPLE"]
        verify_discovery_binding(
            DiscoveryDocument.model_validate(discovery_payload),
            fetched_url="https://shop.example/.well-known/poppy.json",
            metadata=OAuthMetadata.model_validate(oauth_payload),
        )

    @pytest.mark.parametrize(
        "issuer",
        ["https://auth.example/", "https://AUTH.example", "https://other.example"],
    )
    def test_issuer_equality_is_exact_not_url_normalized(
        self,
        discovery_payload: dict[str, Any],
        oauth_payload: dict[str, Any],
        issuer: str,
    ) -> None:
        oauth_payload["issuer"] = issuer
        with pytest.raises(ValueError, match="exactly match"):
            verify_discovery_binding(
                DiscoveryDocument.model_validate(discovery_payload),
                fetched_url="https://shop.example/.well-known/poppy.json",
                metadata=OAuthMetadata.model_validate(oauth_payload),
            )

    def test_attacker_cannot_claim_another_company(
        self, discovery_payload: dict[str, Any], oauth_payload: dict[str, Any]
    ) -> None:
        with pytest.raises(ValueError, match="response host"):
            verify_discovery_binding(
                DiscoveryDocument.model_validate(discovery_payload),
                fetched_url="https://attacker.example/.well-known/poppy.json",
                metadata=OAuthMetadata.model_validate(oauth_payload),
            )

    def test_matching_issuer_must_vouch_for_domain(
        self, discovery_payload: dict[str, Any], oauth_payload: dict[str, Any]
    ) -> None:
        oauth_payload["poppy_domains"] = ["other.example"]
        with pytest.raises(ValueError, match="does not vouch"):
            verify_discovery_binding(
                DiscoveryDocument.model_validate(discovery_payload),
                fetched_url="https://shop.example/.well-known/poppy.json",
                metadata=OAuthMetadata.model_validate(oauth_payload),
            )

    def test_session_discovery_requires_issuer_metadata(
        self, discovery_payload: dict[str, Any]
    ) -> None:
        with pytest.raises(ValueError, match="metadata is required"):
            verify_discovery_binding(
                DiscoveryDocument.model_validate(discovery_payload),
                fetched_url="https://shop.example/.well-known/poppy.json",
            )

    def test_ordinary_website_needs_no_session_authority(self) -> None:
        document = DiscoveryDocument.model_validate(
            {
                "protocol_version": "0.1",
                "organization": {"name": "Shop", "domain": "shop.example"},
                "web": {},
            }
        )
        verify_discovery_binding(
            document, fetched_url="https://shop.example/.well-known/poppy.json"
        )
