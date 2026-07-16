"""Shared plumbing for docker/-image-based tests: build, run, exec, teardown.

A test built on this module should contain only the scenario — never the
subprocess/docker-CLI details — mirroring tests/e2e/baseline/'s "never the
plumbing" philosophy, applied here for tests that build and run a real
Docker image. Nothing here is band-python-kit-specific: any future test
against any other image in docker/ can reuse Image/Container as-is.
"""

from __future__ import annotations

import shlex
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BAND_PYTHON_KIT_DOCKERFILE = REPO_ROOT / "docker" / "band_python_kit" / "Dockerfile"


@dataclass(frozen=True)
class Image:
    """A built Docker image, identified by its unique tag.

    Creation and teardown live on the class (``Image.build``) rather than as
    separate free functions, so a caller never has to remember to pair a
    "build" call with its matching "remove".
    """

    tag: str

    @classmethod
    @contextmanager
    def build(
        cls,
        dockerfile: Path,
        *,
        build_args: dict[str, str] | None = None,
        tag_prefix: str = "pytest-docker",
    ) -> Iterator[Image]:
        """Build ``dockerfile`` against the repo root; yield the image, then remove it."""
        tag = f"{tag_prefix}:{uuid.uuid4().hex[:12]}"
        command = ["docker", "build", "-f", str(dockerfile), "-t", tag]
        for key, value in (build_args or {}).items():
            command += ["--build-arg", f"{key}={value}"]
        command.append(str(REPO_ROOT))
        subprocess.run(command, capture_output=True, text=True, check=True, timeout=600)
        try:
            yield cls(tag)
        finally:
            subprocess.run(
                ["docker", "rmi", "-f", tag], capture_output=True, check=False
            )


@dataclass(frozen=True)
class Container:
    """A running container, with intent-oriented helpers for driving it.

    Creation and teardown live on the class (``Container.run``), and driving
    it wraps the ``docker exec`` plumbing, so a test reads what it's asking
    the container to do, not how the CLI is invoked.
    """

    name: str

    @classmethod
    @contextmanager
    def run(
        cls,
        image: Image | str,
        *,
        name_prefix: str = "pytest-docker",
        env: dict[str, str] | None = None,
        command: list[str] | None = None,
        keep_alive_seconds: int = 300,
    ) -> Iterator[Container]:
        """Start ``image`` detached; yield the container, then remove it."""
        tag = image.tag if isinstance(image, Image) else image
        name = f"{name_prefix}-{uuid.uuid4().hex[:8]}"
        run_command = ["docker", "run", "-d", "--name", name]
        for key, value in (env or {}).items():
            run_command += ["-e", f"{key}={value}"]
        run_command.append(tag)
        run_command += command or ["bash", "-c", f"sleep {keep_alive_seconds}"]
        subprocess.run(
            run_command, capture_output=True, text=True, check=True, timeout=30
        )
        try:
            yield cls(name)
        finally:
            subprocess.run(
                ["docker", "rm", "-f", name], capture_output=True, check=False
            )

    def exec(self, command: str, *, timeout: int = 60) -> str:
        """Run ``command`` via ``bash -c`` inside the container; return stripped stdout."""
        result = subprocess.run(
            ["docker", "exec", self.name, "bash", "-c", command],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
        return result.stdout.strip()

    def run_python(
        self, code: str, *, interpreter: str = "python3", timeout: int = 60
    ) -> str:
        """Run ``code`` via ``interpreter -c <code>`` inside the container.

        ``code`` is shell-quoted automatically, so it's safe to pass arbitrary
        Python containing quotes — callers never hand-quote shell commands.
        ``interpreter`` is passed through unquoted so a shell variable (e.g.
        ``$BAND_SDK_PYTHON``) still expands.
        """
        return self.exec(f"{interpreter} -c {shlex.quote(code)}", timeout=timeout)
