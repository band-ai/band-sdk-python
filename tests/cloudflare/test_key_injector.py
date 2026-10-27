"""The key injector adds its key only to HTTPS requests for its bound host."""

from __future__ import annotations

import pytest

from tests.cloudflare.client import WorkerRuntime
from tests.cloudflare.contract import BAND_HOST, SETTINGS, Scenario


@pytest.mark.timeout(120)
class TestKeyInjector:
    def test_only_the_bound_https_host_receives_the_key(
        self, runtime: WorkerRuntime
    ) -> None:
        assert runtime.run(Scenario.KEY_INJECTOR) == {
            "statuses": {"allowed": 302, "other_host": 403, "insecure": 403},
            "upstream": [
                {
                    "url": f"https://{BAND_HOST}/redirect",
                    "key": SETTINGS["BAND_API_KEY"],
                    "redirect": "manual",
                }
            ],
        }
