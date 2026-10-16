"""Reject characters URL parsers can discard or interpret inconsistently."""

from __future__ import annotations

from typing import Any

import pytest
from bandpap.discovery import DiscoveryDocument, verify_discovery_binding


class TestDiscoveryControls:
    @pytest.mark.parametrize("codepoint", [*range(32), 127])
    @pytest.mark.parametrize("location", ["endpoint", "issuer", "fetched_url"])
    def test_control_characters_never_cross_the_discovery_boundary(
        self,
        discovery_payload: dict[str, Any],
        codepoint: int,
        location: str,
    ) -> None:
        control = chr(codepoint)
        with pytest.raises(ValueError, match="controls"):
            match location:
                case "endpoint":
                    discovery_payload["agent"]["protocols"][0]["endpoint"] = (
                        f"https://api.example/{control}path"
                    )
                    DiscoveryDocument.model_validate(discovery_payload)
                case "issuer":
                    discovery_payload["auth"]["issuer"] = (
                        f"{control}https://auth.example"
                    )
                    DiscoveryDocument.model_validate(discovery_payload)
                case "fetched_url":
                    verify_discovery_binding(
                        DiscoveryDocument.model_validate(discovery_payload),
                        fetched_url=f"{control}https://shop.example/.well-known/poppy.json",
                    )
