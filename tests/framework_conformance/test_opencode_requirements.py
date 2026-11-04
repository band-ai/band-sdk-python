"""OpenCode runs on the Anthropic provider, so a keyless run names the key."""

from __future__ import annotations

import pytest

from tests.baseline.adapter import Adapter
from tests.e2e.baseline.settings import Backends, BaselineSettings, LLMCredentials
from tests.e2e.baseline.toolkit.adapters import spec_for
from tests.e2e.baseline.toolkit.deps import requirement_reason


class TestOpencodeRequirements:
    @pytest.mark.parametrize(
        ("anthropic_api_key", "unmet"),
        [
            ("", ["ANTHROPIC_API_KEY not set"]),
            ("key", []),
        ],
    )
    def test_the_lane_requires_the_providers_key(
        self, anthropic_api_key: str, unmet: list[str]
    ) -> None:
        settings = BaselineSettings(
            backends=Backends(opencode_base_url="http://127.0.0.1:4096"),
            llm_credentials=LLMCredentials(anthropic_api_key=anthropic_api_key),
        )

        reasons = [
            reason
            for dep in spec_for(Adapter.OPENCODE).requires
            if (reason := requirement_reason(dep, settings)) is not None
        ]

        assert reasons == unmet
