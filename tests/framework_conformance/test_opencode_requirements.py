"""OpenCode runs on the Anthropic provider, so a keyless run names the key."""

from __future__ import annotations

import pytest

from tests.baseline.adapter import Adapter
from tests.e2e.baseline.settings import Backends, BaselineSettings, LLMCredentials
from tests.e2e.baseline.toolkit.adapters import spec_for
from tests.e2e.baseline.toolkit.deps import Dep, requirement_reason

SERVE_URL = "http://127.0.0.1:4096"


class TestOpencodeRequirements:
    @pytest.mark.parametrize(
        ("anthropic_api_key", "unmet"),
        [
            ("", [Dep.ANTHROPIC]),
            ("key", []),
        ],
    )
    def test_the_lane_requires_the_providers_key(
        self, anthropic_api_key: str, unmet: list[Dep]
    ) -> None:
        settings = BaselineSettings(
            backends=Backends(opencode_base_url=SERVE_URL),
            llm_credentials=LLMCredentials(anthropic_api_key=anthropic_api_key),
        )

        assert [
            dep
            for dep in spec_for(Adapter.OPENCODE).requires
            if requirement_reason(dep, settings) is not None
        ] == unmet
