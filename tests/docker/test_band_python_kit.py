"""Proves the band-python-kit base image's core isolation guarantee.

Gated behind DOCKER_TESTS_ENABLED (see tests/conftest.py); never runs by
default, including in CI. Fixtures live in tests/docker/conftest.py /
tests/docker/toolkit/ — this file is scenario only.
"""

from __future__ import annotations

import pytest

from tests.docker.toolkit.docker_cli import BUILD_TIMEOUT_S, Container

SDK_PYTHON = "$BAND_SDK_PYTHON"

# Ancient enough to be a real, meaningful API break against the SDK venv's
# own httpx (0.28.1 as of writing) — not just a different patch version.
CONFLICTING_HTTPX_VERSION = "0.13.3"

# The repo-wide 30s pytest-timeout default (pyproject.toml) covers fixture
# setup too, so it bounds the session-scoped image build — well under
# Image.build()'s own BUILD_TIMEOUT_S, on a machine with no cached layers.
# +60 margin for the rest of this test's (fast) body.
_BUILD_TEST_TIMEOUT = BUILD_TIMEOUT_S + 60


@pytest.mark.docker_build
@pytest.mark.timeout(_BUILD_TEST_TIMEOUT)
def test_conflicting_customer_pin_does_not_break_sdk_venv(
    band_python_kit_container: Container,
) -> None:
    """A conflicting dep pin in a second, customer-simulating venv must not
    affect the baked SDK venv's own transport deps or ability to import."""
    baseline_httpx = band_python_kit_container.run_python(
        "import httpx; print(httpx.__version__)", interpreter=SDK_PYTHON
    )
    assert baseline_httpx, "expected the baked SDK venv to report an httpx version"

    # Simulate the customer venv INT-978's launcher creates at sandbox
    # runtime, with a deliberately ancient, conflicting httpx pin.
    band_python_kit_container.exec(
        "python3 -m venv /tmp/customer-venv && "
        f"/tmp/customer-venv/bin/pip install --quiet 'httpx=={CONFLICTING_HTTPX_VERSION}'"
    )
    customer_httpx = band_python_kit_container.run_python(
        "import httpx; print(httpx.__version__)",
        interpreter="/tmp/customer-venv/bin/python",
    )
    assert customer_httpx == CONFLICTING_HTTPX_VERSION, (
        "expected the customer venv's conflicting pin to actually take, "
        f"got {customer_httpx!r}"
    )

    # The baked SDK venv must be completely unaffected: same httpx version,
    # SDK still importable.
    sdk_httpx_after = band_python_kit_container.run_python(
        "import httpx; print(httpx.__version__)", interpreter=SDK_PYTHON
    )
    assert sdk_httpx_after == baseline_httpx, (
        "conflicting customer pin leaked into the SDK venv: expected "
        f"{baseline_httpx!r}, got {sdk_httpx_after!r}"
    )

    band_version = band_python_kit_container.run_python(
        "import band; print(band.__version__)", interpreter=SDK_PYTHON
    )
    assert band_version, "band must still import from the SDK venv"
