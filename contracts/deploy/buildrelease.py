#!/usr/bin/env python3
"""Build a self-contained engine and Python wheel into a new release directory."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

# Direct isolated invocation trusts this checkout or sealed release root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from contracts.deploy.checkbandengine import verify as verify_engine
from contracts.deploy.environment import BuildSettings
from contracts.deploy.releaseverification import verify_release
from contracts.deploy.toolchain import Toolchain, select

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    target = args.destination.absolute()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT):
        parser.error(
            "Release builds require a clean source tree, including untracked files"
        )
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    if sys.version_info[:2] != (3, 12):
        parser.error("Release builds require Python 3.12")
    try:
        toolchain = select(ROOT / "contracts")
    except ValueError as exc:
        parser.error(str(exc))
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    epoch = int(
        subprocess.check_output(
            ["git", "show", "-s", "--format=%ct", revision], cwd=ROOT
        )
    )
    archive = subprocess.check_output(
        ["git", "archive", "--format=tar", revision], cwd=ROOT
    )
    environment = BuildSettings().environment(toolchain["bin_paths"], mix_env="prod")
    environment.update(
        ERL_COMPILER_OPTIONS="[deterministic]",
        SOURCE_DATE_EPOCH=str(epoch),
        PYTHONHASHSEED="0",
    )
    with tempfile.TemporaryDirectory(prefix="contract-release-build-") as directory:
        scratch = Path(directory)
        source = scratch / "source"
        source.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as files:
            files.extractall(source, filter="data")
        assets = source / "contracts"
        verify_engine(assets / "engines/band-v1")
        # A fresh interpreter environment installs only the hash-locked build tools.
        subprocess.run(
            [sys.executable, "-m", "venv", str(scratch / "venv")],
            cwd=source,
            env=environment,
            check=True,
        )
        python = scratch / "venv/bin/python"
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--require-hashes",
                "--only-binary=:all:",
                "-r",
                str(assets / "requirements-build.lock"),
            ],
            cwd=source,
            env=environment,
            check=True,
        )
        staged = scratch / "release"
        staged.mkdir()
        subprocess.run(
            ["mix", "deps.get", "--only", "prod", "--check-locked"],
            cwd=assets / "contract-lab",
            env=environment,
            check=True,
        )
        subprocess.run(
            ["mix", "release", "contract_engine", "--path", str(staged / "engine")],
            cwd=assets / "contract-lab",
            env=environment,
            check=True,
        )
        subprocess.run(
            ["mix", "deps.get", "--only", "prod", "--check-locked"],
            cwd=assets / "engines/band-v1/contract-lab",
            env=environment,
            check=True,
        )
        subprocess.run(
            [
                "mix",
                "release",
                "contract_engine",
                "--path",
                str(staged / "engine-band-v1"),
            ],
            cwd=assets / "engines/band-v1/contract-lab",
            env=environment,
            check=True,
        )
        subprocess.run(
            [
                str(python),
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                "--outdir",
                str(staged / "wheels"),
                str(source),
            ],
            cwd=source,
            env=environment,
            check=True,
        )
        subprocess.run(
            [
                "uv",
                "export",
                "--locked",
                "--no-default-groups",
                "--extra",
                "contracts",
                "--no-emit-workspace",
                "--no-editable",
                "--no-header",
                "--no-annotate",
                "--format",
                "requirements.txt",
                "--output-file",
                str(staged / "requirements-runtime.lock"),
            ],
            cwd=source,
            env=environment,
            check=True,
        )
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "download",
                "--require-hashes",
                "--only-binary=:all:",
                "--dest",
                str(staged / "dependencies"),
                "-r",
                str(staged / "requirements-runtime.lock"),
            ],
            cwd=source,
            env=environment,
            check=True,
        )
        assemble(source, staged, revision, epoch, environment, toolchain)
        verify_release(staged)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staged, target)
    manifest = json.loads((target / "manifest.json").read_text())
    logger.info(
        "Built %s files at %s from %s", len(manifest["files"]), target, revision
    )


def assemble(
    source: Path,
    target: Path,
    revision: str,
    epoch: int,
    environment: dict[str, str],
    toolchain: Toolchain,
) -> None:
    assets = source / "contracts"
    for name in (
        "requirements-build.lock",
        "requirements-band.lock",
        "requirements-system.json",
    ):
        shutil.copy2(assets / name, target / name)
    shutil.copy2(source / "uv.lock", target / "uv.lock")
    shutil.copy2(source / "pyproject.toml", target / "sdk-pyproject.toml")
    shutil.copytree(assets / "contract-lab/contracts", target / "contracts")
    shutil.copytree(
        assets / "spec",
        target / "contracts/spec",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(assets / "agent-runtime/examples", target / "examples")
    shutil.copytree(
        assets / "deploy",
        target / "contracts/deploy",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copy2(assets / "__init__.py", target / "contracts/__init__.py")
    shutil.copy2(
        assets / "engines/band-v1/SOURCE.json", target / "engine-band-v1-source.json"
    )
    (target / "locks").mkdir()
    for project in ("header-spikes", "contract-lab", "room-sim"):
        lock = assets / project / "mix.lock"
        if lock.exists():
            shutil.copy2(lock, target / "locks" / (project + ".mix.lock"))
    manifest: dict[str, Any] = {
        "profile": "band-contract/release/1",
        "target": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "engine_build": subprocess.check_output(
            ["elixir", "--version"], cwd=source, env=environment, text=True
        ).strip(),
        "toolchain": {key: toolchain[key] for key in ("elixir", "erlang")},
        "source_commit": revision,
        "source_dirty": False,
        "distribution": "band-sdk",
        "build": {
            "isolated_source": True,
            "isolated_python_build_tools": True,
            "hash_locked_python_build_tools": True,
            "source_date_epoch": epoch,
        },
        "files": {},
        "modes": {},
    }
    for path in [target, *target.rglob("*")]:
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
            os.utime(path, (epoch, epoch))
    for path in sorted(target.rglob("*")):
        if path.is_file():
            manifest["files"][str(path.relative_to(target))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            manifest["modes"][str(path.relative_to(target))] = (
                path.stat().st_mode & 0o777
            )
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (target / "manifest.json").chmod(0o644)
    os.utime(target / "manifest.json", (epoch, epoch))
    # Directory mtimes change as their children are copied or created.
    for path in [target, *(p for p in target.rglob("*") if p.is_dir())]:
        os.utime(path, (epoch, epoch))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
