"""Proves the built band-python-kit image's runtime contract.

The image carries its pinned uv, keeps the baked SDK venv isolated from a
customer venv, runs as the agent user, and fails loudly without a workspace —
for the default target, the ``cli`` target, and a custom ``BAND_SDK_HOME``.

Gated behind DOCKER_TESTS_ENABLED (see tests/conftest.py); CI's docker-kit job
opts in. Fixtures live in tests/docker/conftest.py /
tests/docker/toolkit/ — this file is scenario only.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.docker.toolkit.docker_cli import (
    BAND_PYTHON_KIT_DOCKERFILE,
    BUILD_TEST_TIMEOUT_S,
    Container,
    Image,
)

SDK_PYTHON = "$BAND_SDK_PYTHON"
SDK_UV = "$BAND_SDK_UV"

# Ancient enough to be a real, meaningful API break against the SDK venv's
# own httpx (0.28.1 as of writing) — not just a different patch version.
CONFLICTING_HTTPX_VERSION = "0.13.3"


@pytest.mark.docker_build
@pytest.mark.timeout(BUILD_TEST_TIMEOUT_S)
class TestKitImageRuntimeContract:
    def test_pinned_uv_available_in_runtime_image(
        self,
        band_python_kit_container: Container,
    ) -> None:
        """The runtime image must carry the build's pinned uv at $BAND_SDK_UV —
        the sandbox launcher runs it for locked customer-venv sync and must never
        download uv at runtime. Runs as the non-root agent user, the launcher's
        image-configured uid."""
        version = band_python_kit_container.exec(f'"{SDK_UV}" --version', user="agent")
        assert version.startswith("uv "), (
            f"expected {SDK_UV} to run and report a version, got {version!r}"
        )

    def test_conflicting_customer_pin_does_not_break_sdk_venv(
        self,
        band_python_kit_container: Container,
    ) -> None:
        """A conflicting dep pin in a second, customer-simulating venv must not
        affect the baked SDK venv's own transport deps or ability to import."""
        baseline_httpx = band_python_kit_container.run_python(
            "import httpx; print(httpx.__version__)", interpreter=SDK_PYTHON
        )
        assert baseline_httpx, "expected the baked SDK venv to report an httpx version"

        # Simulate the customer venv created at sandbox runtime, with a
        # deliberately ancient, conflicting httpx pin.
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

    def test_workload_default_identity_and_launcher_failure(
        self, band_python_kit_image: Image, band_python_kit_container: Container
    ) -> None:
        assert band_python_kit_container.exec("id -u") == "1000"
        result = subprocess.run(
            ["docker", "run", "--rm", band_python_kit_image.tag],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0
        assert "WORKSPACE_DIR is not set" in result.stderr

    def test_cli_workload_retains_agent_identity_and_real_tools(self) -> None:
        with (
            Image.build(BAND_PYTHON_KIT_DOCKERFILE, target="cli") as image,
            Container.run(image) as container,
        ):
            assert container.exec("id -u") == "1000"
            assert container.exec("codex --version").startswith("codex-cli ")
            assert "Claude Code" in container.exec("claude --version")

    def test_custom_sdk_home_preserves_launcher_and_isolation(self) -> None:
        with (
            Image.build(
                BAND_PYTHON_KIT_DOCKERFILE,
                build_args={"BAND_SDK_HOME": "/opt/band-custom"},
            ) as image,
            Container.run(image) as container,
        ):
            assert (
                container.exec('"$BAND_SDK_PYTHON" -c "import sys; print(sys.prefix)"')
                == "/opt/band-custom/venv"
            )
            assert container.exec('"$BAND_SDK_UV" --version').startswith("uv ")
            result = subprocess.run(
                ["docker", "run", "--rm", image.tag],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode != 0
            assert "WORKSPACE_DIR is not set" in result.stderr
