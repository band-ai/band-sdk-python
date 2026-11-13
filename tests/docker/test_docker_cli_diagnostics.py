"""A failed Docker CLI call reports the command's own diagnostic.

Runs without Docker: the ``failing_docker`` fixture stands in for the CLI.
"""

from __future__ import annotations

import pytest

from tests.docker.toolkit.docker_cli import BAND_PYTHON_KIT_DOCKERFILE, Container, Image


class TestDockerFailureDiagnostics:
    def test_failed_image_build_reports_the_builder_diagnostic(
        self, failing_docker: str
    ) -> None:
        with (
            pytest.raises(RuntimeError, match=failing_docker),
            Image.build(BAND_PYTHON_KIT_DOCKERFILE),
        ):
            pytest.fail("a failed build must not yield an image")

    def test_failed_container_start_reports_the_docker_diagnostic(
        self, failing_docker: str
    ) -> None:
        with (
            pytest.raises(RuntimeError, match=failing_docker),
            Container.run(Image("pytest-docker:missing")),
        ):
            pytest.fail("a failed start must not yield a container")

    def test_failed_exec_reports_the_docker_diagnostic(
        self, failing_docker: str
    ) -> None:
        with pytest.raises(RuntimeError, match=failing_docker):
            Container("pytest-docker-missing").exec("true")
