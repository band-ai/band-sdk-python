"""A container Docker creates but cannot start is still removed.

Gated behind DOCKER_TESTS_ENABLED (see tests/conftest.py); CI's docker-kit job
opts in.
"""

from __future__ import annotations

import subprocess
import uuid

import pytest

from tests.docker.toolkit.docker_cli import BUILD_TEST_TIMEOUT_S, Container, Image


def containers_named(prefix: str) -> list[str]:
    listing = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"name={prefix}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return listing.stdout.split()


@pytest.mark.docker_build
@pytest.mark.timeout(BUILD_TEST_TIMEOUT_S)
class TestFailedContainerStartIsRemoved:
    def test_unstartable_container_is_not_left_behind(
        self, band_python_kit_image: Image
    ) -> None:
        prefix = f"pytest-unstartable-{uuid.uuid4().hex[:8]}"
        try:
            with (
                pytest.raises(RuntimeError),
                Container.run(
                    band_python_kit_image,
                    name_prefix=prefix,
                    entrypoint="/nonexistent-entrypoint",
                ),
            ):
                pytest.fail("an unstartable container must not be yielded")
            assert containers_named(prefix) == []
        finally:
            leaked = containers_named(prefix)
            if leaked:
                subprocess.run(["docker", "rm", "-f", *leaked], check=False)
