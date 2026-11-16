"""The root conftest's unit-test isolation spares every live test, wherever it lives."""

from __future__ import annotations

import pytest

from tests.paths import REPO_ROOT

# One rate-limited response, read through the generated client's real backoff
# rule: a live test waits out the server's Retry-After, a unit test does not.
SUBRUN_TESTS = """
import httpx
import pytest
from band_rest.core import http_client

RATE_LIMITED = httpx.Response(429, headers={"retry-after": "7"})


def backoff():
    return http_client._retry_timeout(response=RATE_LIMITED, retries=1)


@pytest.mark.e2e
def test_marked_live_test_keeps_the_real_backoff():
    assert backoff() == 7


def test_unit_test_retries_at_once():
    assert backoff() == 0
"""


class TestLiveTestIsolation:
    def test_e2e_marker_outside_tests_e2e_keeps_the_real_backoff(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A subprocess keeps the root conftest's configure/unconfigure hooks
        # out of this session; the open gate keeps the marked test from skipping.
        monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
        monkeypatch.setenv("E2E_TESTS_ENABLED", "true")
        pytester.makeini("[pytest]\nmarkers =\n    e2e: live")
        pytester.makepyfile(SUBRUN_TESTS)

        result = pytester.runpytest_subprocess("-p", "tests.conftest", "-q")

        result.assert_outcomes(passed=2)
