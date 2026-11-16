"""A container Docker creates but cannot start is still removed.

Gated behind DOCKER_TESTS_ENABLED (see tests/conftest.py); CI's docker-kit job
opts in. Fixtures live in tests/docker/conftest.py /
tests/docker/toolkit/ — this file is scenario only.
"""

from __future__ import annotations

import pytest

from tests.docker.toolkit.docker_cli import (
    BUILD_TEST_TIMEOUT_S,
    Container,
    Image,
    container_ids,
)

# runc's error once the container exists but its process cannot start.
START_FAILURE = "unable to start container process"


@pytest.mark.docker_build
@pytest.mark.timeout(BUILD_TEST_TIMEOUT_S)
class TestFailedContainerStartIsRemoved:
    def test_unstartable_container_is_not_left_behind(
        self, band_python_kit_image: Image, container_name_prefix: str
    ) -> None:
        with (
            pytest.raises(RuntimeError, match=START_FAILURE),
            Container.run(
                band_python_kit_image,
                name_prefix=container_name_prefix,
                entrypoint="/nonexistent-entrypoint",
            ),
        ):
            pytest.fail("an unstartable container must not be yielded")
        assert container_ids(container_name_prefix) == []
