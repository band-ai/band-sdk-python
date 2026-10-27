"""The key injector replaces the key only on HTTPS requests for its exact bound host."""

from __future__ import annotations

from typing import Any

import pytest

from tests.cloudflare.client import WorkerRuntime
from tests.cloudflare.contract import BAND_HOST, SETTINGS, UPSTREAM_BODY, Scenario


@pytest.mark.timeout(120)
class TestKeyInjector:
    def test_only_the_bound_https_host_receives_the_real_key(
        self, runtime: WorkerRuntime, constants: dict[str, Any]
    ) -> None:
        forbidden = [403, constants["forbidden_upstream"]]
        assert runtime.run(Scenario.KEY_INJECTOR) == {
            "outcomes": {
                "allowed": [302, UPSTREAM_BODY],
                "other_host": forbidden,
                "other_port": forbidden,
                "lookalike_host": forbidden,
                "insecure": forbidden,
            },
            "upstream": [
                {
                    "url": f"https://{BAND_HOST}/redirect",
                    "key": SETTINGS["BAND_API_KEY"],
                    "redirect": "manual",
                }
            ],
        }
