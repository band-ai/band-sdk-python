"""Proves the band-python-kit base image's core isolation guarantee.

Builds the real image and a real conflicting-pin scenario via Docker — the
only way to actually prove isolation, as opposed to asserting it from the
Dockerfile's text. Gated behind DOCKER_TESTS_ENABLED (see conftest.py); never
runs by default, including in CI.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker_build

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "docker" / "band_python_kit" / "Dockerfile"

# Ancient enough to be a real, meaningful API break against the SDK venv's
# own httpx (0.28.1 as of writing) — not just a different patch version.
CONFLICTING_HTTPX_VERSION = "0.13.3"


def _docker_exec(container: str, command: str) -> str:
    result = subprocess.run(
        ["docker", "exec", container, "bash", "-c", command],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return result.stdout


@pytest.fixture(scope="session")
def band_python_kit_image() -> str:
    tag = f"band-python-kit-test:{uuid.uuid4().hex[:12]}"
    subprocess.run(
        ["docker", "build", "-f", str(DOCKERFILE), "-t", tag, str(REPO_ROOT)],
        capture_output=True,
        text=True,
        check=True,
        timeout=600,
    )
    yield tag
    subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, check=False)


@pytest.fixture
def band_python_kit_container(band_python_kit_image: str):
    container = f"band-python-kit-pin-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container,
            band_python_kit_image,
            "bash",
            "-c",
            "sleep 60",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    yield container
    subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)


def test_conflicting_customer_pin_does_not_break_sdk_venv(
    band_python_kit_container: str,
) -> None:
    """A conflicting dep pin in a second, customer-simulating venv must not
    affect the baked SDK venv's own transport deps or ability to import."""
    container = band_python_kit_container

    baseline_httpx = _docker_exec(
        container,
        '$BAND_SDK_PYTHON -c "import httpx; print(httpx.__version__)"',
    ).strip()
    assert baseline_httpx, "expected the baked SDK venv to report an httpx version"

    # Simulate the customer venv INT-978's launcher creates at sandbox
    # runtime, with a deliberately ancient, conflicting httpx pin.
    _docker_exec(
        container,
        "python3 -m venv /tmp/customer-venv && "
        f"/tmp/customer-venv/bin/pip install --quiet 'httpx=={CONFLICTING_HTTPX_VERSION}'",
    )
    customer_httpx = _docker_exec(
        container,
        '/tmp/customer-venv/bin/python -c "import httpx; print(httpx.__version__)"',
    ).strip()
    assert customer_httpx == CONFLICTING_HTTPX_VERSION, (
        "expected the customer venv's conflicting pin to actually take, "
        f"got {customer_httpx!r}"
    )

    # The baked SDK venv must be completely unaffected: same httpx version,
    # SDK still importable.
    sdk_httpx_after = _docker_exec(
        container,
        '$BAND_SDK_PYTHON -c "import httpx; print(httpx.__version__)"',
    ).strip()
    assert sdk_httpx_after == baseline_httpx, (
        "conflicting customer pin leaked into the SDK venv: expected "
        f"{baseline_httpx!r}, got {sdk_httpx_after!r}"
    )

    band_version = _docker_exec(
        container,
        '$BAND_SDK_PYTHON -c "import band; print(band.__version__)"',
    ).strip()
    assert band_version, "band must still import from the SDK venv"
