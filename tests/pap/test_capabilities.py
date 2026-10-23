"""Explicit intersection of advertised and implemented PAP features."""

from __future__ import annotations

import pytest
from bandpap.discovery import (
    DiscoveryDocument,
    OAuthMetadata,
    select_capabilities,
    verify_discovery_binding,
)


class TestCapabilities:
    def test_unknown_protocol_and_extension_never_fall_back(
        self, discovery_payload: dict
    ) -> None:
        discovery_payload["agent"]["protocols"][0]["type"] = "future"
        discovery_payload["extensions"] = {
            "operations": {"version": "2", "endpoint": "not a URL"}
        }
        capabilities = select_capabilities(
            DiscoveryDocument.model_validate(discovery_payload),
            protocols=frozenset({"poppy"}),
            api_types=frozenset(),
            operations_version="1",
        )
        assert capabilities.conversations == ()
        assert capabilities.operations is None

    def test_supported_resource_scopes_and_signin_fields_are_retained(
        self, discovery_payload: dict
    ) -> None:
        discovery_payload["auth"].update(
            {
                "direct": {"scopes": ["poppy:read"]},
                "mediated": {
                    "endpoint": "https://auth.example/signin",
                    "fields": [
                        {"name": "password", "label": "Password", "secret": True}
                    ],
                    "scopes": [],
                },
            }
        )
        discovery_payload["extensions"] = {
            "operations": {
                "version": "1",
                "endpoint": "https://api.example/ops",
                "resource": "https://api.example/",
            }
        }
        document = DiscoveryDocument.model_validate(discovery_payload)
        capabilities = select_capabilities(
            document,
            protocols=frozenset({"poppy"}),
            api_types=frozenset(),
            operations_version="1",
        )
        assert capabilities.operations.resource == "https://api.example/"
        assert document.auth.direct.scopes == ("poppy:read",)
        assert document.auth.mediated.fields[0].secret
        assert (
            select_capabilities(
                document, protocols=frozenset(), api_types=frozenset()
            ).operations
            is None
        )

    @pytest.mark.parametrize(
        ("method", "field"),
        [
            ("direct", "authorization_endpoint"),
            ("device", "device_authorization_endpoint"),
        ],
    )
    def test_advertised_signin_requires_oauth_endpoint(
        self, discovery_payload: dict, oauth_payload: dict, method: str, field: str
    ) -> None:
        discovery_payload["auth"][method] = {"scopes": []}
        document = DiscoveryDocument.model_validate(discovery_payload)
        with pytest.raises(ValueError, match="endpoint"):
            verify_discovery_binding(
                document,
                fetched_url="https://shop.example/",
                metadata=OAuthMetadata.model_validate(oauth_payload),
            )
        oauth_payload[field] = "https://auth.example/signin"
        verify_discovery_binding(
            document,
            fetched_url="https://shop.example/",
            metadata=OAuthMetadata.model_validate(oauth_payload),
        )
