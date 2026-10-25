"""Helpers for Core coverage workflow and digest checks."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

import yaml

from tests.paths import REPO_ROOT
from tests.xdistworker import worker_scoped

WORKFLOW_PATH = REPO_ROOT / ".github/workflows/python-core-coverage.yml"
# The guard script below shells out to a real `uv run`, which needs a real,
# writable cache dir -- don't trust an inherited $HOME: another test earlier
# in the same pytest process may have repointed it (e.g. at a container-only
# path that doesn't exist on this machine). Two xdist workers must not share
# that directory either, so it is scoped to ``PYTEST_XDIST_WORKER``.


def pin_guard_uv_cache_dir() -> Path:
    """Writable uv cache for the pin-guard subprocess, unique per xdist worker."""
    return Path(tempfile.gettempdir()) / worker_scoped(
        "band-sdk-python-pin-guard-uv-cache"
    )


def load_workflow() -> dict[str, Any]:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _step_names(workflow: dict[str, Any], *, job: str = "coverage") -> list[str | None]:
    return [step.get("name") for step in workflow["jobs"][job]["steps"]]


def _step(
    workflow: dict[str, Any], name: str, *, job: str = "coverage"
) -> dict[str, Any]:
    steps = workflow["jobs"][job]["steps"]
    return next(step for step in steps if step.get("name") == name)


def _pin_guard_script(run_text: str) -> str:
    """Isolate the pre-release guard invocation from the pin step's run text."""
    lines = run_text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("VERSION="))
    end = next(i for i, line in enumerate(lines) if 'echo "version=$version"' in line)
    return "\n".join(lines[start:end])


def _run_bash(
    script: str, *, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Execute a shell script the same way a workflow's bash step runs it."""
    bash = "bash"
    if sys.platform == "win32":
        bash = str(Path(os.environ["ProgramFiles"]) / "Git" / "bin" / "bash.exe")

    return subprocess.run(
        [bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        check=False,
    )


def _run_pin_guard(version: str) -> subprocess.CompletedProcess[str]:
    """Execute the real guard script from the workflow against a given version string."""
    workflow = load_workflow()
    run_text = _step(workflow, "Resolve pinned band-sdk-core version")["run"]
    core_tag_prefix = workflow["jobs"]["coverage"]["env"]["CORE_TAG_PREFIX"]
    script = f'version="{version}"\n{_pin_guard_script(run_text)}'
    return _run_bash(
        script,
        env={
            "CORE_TAG_PREFIX": core_tag_prefix,
            "UV_CACHE_DIR": str(pin_guard_uv_cache_dir()),
        },
    )


def _run_step(
    step_name: str, *, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Execute a coverage-job step's real `run:` text as a subprocess from `cwd`."""
    run_text = _step(load_workflow(), step_name)["run"]
    return _run_bash(run_text, cwd=cwd, env=env)


def _load_digest_module() -> Any:
    """Load post-core-coverage-digest.py, which has no package, as a module."""
    script_path = REPO_ROOT / ".github/scripts/post-core-coverage-digest.py"
    spec = spec_from_file_location("post_core_coverage_digest", script_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
