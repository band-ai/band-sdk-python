from __future__ import annotations

import yaml

from tests.paths import CLAUDE_MIXIN_DIR


class TestCustody:
    def test_band_key_injection_stays_on_the_approved_band_host(self) -> None:
        spec = yaml.safe_load((CLAUDE_MIXIN_DIR / "band-claude.yaml").read_text())
        credentials = [
            c["config"]
            for c in spec["capabilities"]
            if c["type"] == "com.docker.sandbox/credential@1"
        ]
        assert len(credentials) == 1
        request = credentials[0]
        assert request["service"] == "band"
        assert request["apiKey"]["proxyManaged"] is True
        assert request["apiKey"]["inject"] == [
            {"domain": "app.band.ai", "header": "X-API-Key", "format": "%s"}
        ]
        environment = yaml.safe_load(
            (CLAUDE_MIXIN_DIR / "sbxenv.example.yaml").read_text()
        )
        assert environment["bindings"]["band"]["apiKey"]["domains"] == ["app.band.ai"]
        assert "value" not in environment["secrets"]["band"]
