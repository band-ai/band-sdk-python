"""Release metadata fixtures and script loading."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from zipfile import ZipFile

import pytest
import yaml

from tests.paths import CI_SCRIPTS, REPO_ROOT


@pytest.fixture
def releasecheck() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "releasecheck", CI_SCRIPTS / "releasecheck.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def release_tree(tmp_path: Path) -> Path:
    (tmp_path / "packages/band-mcp").mkdir(parents=True)
    (tmp_path / "release-please-config.json").write_text(
        json.dumps(
            {
                "plugins": [
                    {"type": "linked-versions", "components": ["band-sdk", "band-mcp"]}
                ]
            }
        )
    )
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="9.3.0"\n')
    (tmp_path / "packages/band-mcp/pyproject.toml").write_text(
        '[project]\nversion="9.3.0"\ndependencies=["band-sdk>=9.3.0"]\n'
    )
    (tmp_path / ".release-please-manifest.json").write_text(
        json.dumps({".": "9.3.0", "packages/band-mcp": "9.3.0"})
    )
    return tmp_path


@pytest.fixture
def wheel_index(tmp_path: Path) -> Callable[..., Path]:
    """Build small valid wheels for real offline resolver decisions."""

    def build(name: str, version: str, requirements: tuple[str, ...] = ()) -> Path:
        wheel = tmp_path / f"{name}-{version}-py3-none-any.whl"
        info = f"{name}-{version}.dist-info"
        metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        metadata += "".join(f"Requires-Dist: {value}\n" for value in requirements)
        with ZipFile(wheel, "w") as archive:
            archive.writestr(f"{info}/METADATA", metadata)
            archive.writestr(
                f"{info}/WHEEL",
                "Wheel-Version: 1.0\nGenerator: release-fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            )
            archive.writestr(f"{info}/RECORD", "")
        return wheel

    return build


@pytest.fixture
def candidate_gate(release_tree: Path, tmp_path: Path) -> Path:
    """Run the shell gate with controlled GitHub and interpreter executables."""
    scripts = release_tree / ".github/scripts"
    scripts.mkdir(parents=True)
    shutil.copy(CI_SCRIPTS / "releasecheck.py", scripts / "releasecheck.py")
    manifest = release_tree / ".release-please-manifest.json"
    candidate = manifest.read_text()
    manifest.write_text(json.dumps({".": "9.2.0", "packages/band-mcp": "2.2.15"}))
    for args in (
        ["init"],
        ["add", ".release-please-manifest.json"],
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-m",
            "baseline",
        ],
    ):
        subprocess.run(
            ["git", *args], cwd=release_tree, check=True, capture_output=True
        )
    manifest.write_text(candidate)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "gh").write_text('#!/usr/bin/env bash\nprintf "%s" "$FIXTURE_HEAD"\n')
    (binaries / "uv").write_text(
        f'#!/usr/bin/env bash\n[[ "$1 $2 $3" = "run --all-packages python" ]] || exit 90\nshift 3\nexec "{sys.executable}" "$@"\n'
    )
    (binaries / "gh").chmod(0o755)
    (binaries / "uv").chmod(0o755)
    return binaries


@pytest.fixture
def mcp_publish_steps() -> dict[str, str]:
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/band-mcp-publish.yml").read_text()
    )
    return {
        step["name"]: step["run"]
        for step in workflow["jobs"]["build"]["steps"]
        if "run" in step
    }


@pytest.fixture
def historical_floor_script(mcp_publish_steps: dict[str, str]) -> str:
    return mcp_publish_steps[
        "Wait for the declared band-sdk floor to be indexed on PyPI"
    ].split("# Dry-run")[0]


@pytest.fixture
def run_candidate_gate(
    release_tree: Path, candidate_gate: Path
) -> Callable[..., subprocess.CompletedProcess[bytes]]:
    def run(event: str, head: str, queue: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["bash", str(CI_SCRIPTS / "check-release-candidate.sh")],
            cwd=release_tree,
            env={
                **os.environ,
                "PATH": str(candidate_gate) + os.pathsep + os.environ["PATH"],
                "EVENT_NAME": event,
                "PR_HEAD": head,
                "FIXTURE_HEAD": head,
                "QUEUE_REF": queue,
                "REPO": "fixture/repo",
                "BASE_SHA": "HEAD",
            },
            capture_output=True,
            check=False,
        )

    return run


@pytest.fixture
def publisher_install(
    tmp_path: Path, mcp_publish_steps: dict[str, str]
) -> Callable[[Path], subprocess.CompletedProcess[str]]:
    """Execute production wheel-constraint generation and the real install step offline."""
    environment = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path),
        "UV_NO_INDEX": "1",
        "UV_FIND_LINKS": str(tmp_path),
        "UV_PYTHON": sys.executable,
    }

    def install(wheel: Path) -> subprocess.CompletedProcess[str]:
        subprocess.run(
            [
                sys.executable,
                str(CI_SCRIPTS / "releasecheck.py"),
                "--wheel",
                str(wheel),
                "--output",
                str(tmp_path / "mcp-sdk-floor.txt"),
            ],
            check=True,
            capture_output=True,
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                mcp_publish_steps["Install band-mcp with band-sdk resolved from PyPI"],
            ],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    return install
