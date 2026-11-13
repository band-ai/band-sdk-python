"""A failed Docker CLI call reports the command's own diagnostic.

Runs without Docker: the ``failing_docker`` fixture stands in for the CLI.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack

import pytest

from tests.docker.toolkit.docker_cli import BAND_PYTHON_KIT_DOCKERFILE, Container, Image

MISSING_CONTAINER = Container("pytest-docker-missing")

# Each entry point drives one Docker command; context managers enter the stack.
DOCKER_CALLS: dict[str, Callable[[ExitStack], object]] = {
    "image-build": lambda stack: stack.enter_context(
        Image.build(BAND_PYTHON_KIT_DOCKERFILE)
    ),
    "container-run": lambda stack: stack.enter_context(
        Container.run(Image("pytest-docker:missing"))
    ),
    "exec": lambda _: MISSING_CONTAINER.exec("true"),
    "exec-background": lambda _: MISSING_CONTAINER.exec_background("true"),
}


class TestDockerFailureDiagnostics:
    @pytest.mark.parametrize("call", DOCKER_CALLS.values(), ids=DOCKER_CALLS.keys())
    def test_failure_reports_the_docker_diagnostic(
        self, failing_docker: str, call: Callable[[ExitStack], object]
    ) -> None:
        with ExitStack() as stack, pytest.raises(RuntimeError, match=failing_docker):
            call(stack)
