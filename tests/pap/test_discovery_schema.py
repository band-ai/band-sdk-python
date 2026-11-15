"""Enforce discovery requirements without rejecting additive draft fields."""

from __future__ import annotations

from typing import Any

import pytest
from bandpap.discovery import DiscoveryDocument, OAuthMetadata
from pydantic import ValidationError


class TestDiscoverySchema:
    @pytest.mark.parametrize("version", ["1.0", "garbage", "0", "0.1.2"])
    def test_rejects_invalid_or_unsupported_version(
        self, discovery_payload: dict[str, Any], version: str
    ) -> None:
        discovery_payload["protocol_version"] = version
        with pytest.raises(ValidationError):
            DiscoveryDocument.model_validate(discovery_payload)

    def test_accepts_additive_minor_version_and_unknown_fields(
        self, discovery_payload: dict[str, Any]
    ) -> None:
        discovery_payload["protocol_version"] = "0.2"
        discovery_payload["future_capability"] = {"enabled": True}
        document = DiscoveryDocument.model_validate(discovery_payload)
        assert document.agent is not None
        assert (
            document.agent.protocols[0].endpoint == "https://api.example/conversations"
        )

    @pytest.mark.parametrize(
        "endpoint",
        [
            "http://api.example/conversations",
            "https://user:secret@api.example/conversations",
            "https://api.example/conversations#fragment",
            "https://api.example:0/conversations",
            "https://api.example:99999/conversations",
            "https://api.example/has space",
            "https://api.example\\@attacker.example",
        ],
    )
    def test_rejects_ambiguous_or_insecure_endpoints(
        self, discovery_payload: dict[str, Any], endpoint: str
    ) -> None:
        discovery_payload["agent"]["protocols"][0]["endpoint"] = endpoint
        with pytest.raises(ValidationError):
            DiscoveryDocument.model_validate(discovery_payload)

    def test_session_interfaces_cannot_omit_auth(
        self, discovery_payload: dict[str, Any]
    ) -> None:
        discovery_payload.pop("auth")
        with pytest.raises(ValidationError, match="OAuth issuer"):
            DiscoveryDocument.model_validate(discovery_payload)

    def test_requires_at_least_one_interface(
        self, discovery_payload: dict[str, Any]
    ) -> None:
        discovery_payload.pop("agent")
        with pytest.raises(ValidationError, match="requires an agent"):
            DiscoveryDocument.model_validate(discovery_payload)

    def test_rejects_metadata_without_required_proof_algorithm(
        self, oauth_payload: dict[str, Any]
    ) -> None:
        oauth_payload["dpop_signing_alg_values_supported"] = ["RS256"]
        with pytest.raises(ValidationError, match="ES256"):
            OAuthMetadata.model_validate(oauth_payload)
