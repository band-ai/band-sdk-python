#!/usr/bin/env python3
"""Run SDK and contract release gates against a clean, immutable checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

# Direct isolated invocation trusts this checkout or sealed release root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from contracts.deploy.environment import BuildSettings
from contracts.deploy.releaseverification import verify_release
from contracts.deploy.toolchain import select

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output", type=Path, help="new directory for logs, checkout and both releases"
    )
    parser.add_argument("--source-root", type=Path, default=ROOT)
    args = parser.parse_args()
    original = args.source_root.resolve()
    output = args.output.absolute()
    if sys.version_info[:2] != (3, 12):
        parser.error("CI requires Python 3.12")
    top = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], cwd=original, text=True
    ).strip()
    if Path(top).resolve() != original or subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=original
    ):
        parser.error("CI requires the root of a clean committed source tree")
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=original, text=True
    ).strip()
    try:
        toolchain = select(original / "contracts")
    except ValueError as exc:
        parser.error(str(exc))
    environment = BuildSettings().environment(toolchain["bin_paths"], mix_env="test")
    environment.update(CI="true", UV_PROJECT_ENVIRONMENT=str(output / "venv"))
    output.mkdir(parents=True, exist_ok=False)
    logs = output / "logs"
    logs.mkdir()
    result: dict[str, Any] = {
        "profile": "band-contract/ci/1",
        "source_commit": revision,
        "toolchain": {key: toolchain[key] for key in ("elixir", "erlang")},
        "status": "running",
        "gates": [],
        "scope": "canonical SDK, contract engines and release",
    }

    def run(label: str, command: list[str | Path], cwd: Path) -> None:
        log = logs / (label + ".log")
        with log.open("wb") as stream:
            completed = subprocess.run(
                [str(x) for x in command],
                cwd=cwd,
                env=environment,
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=False,
            )
        result["gates"].append(
            {
                "name": label,
                "exit_code": completed.returncode,
                "log": str(log.relative_to(output)),
            }
        )
        logger.info("%s: %s", label, completed.returncode)
        if completed.returncode:
            raise RuntimeError("Gate failed: " + label + "; see " + str(log))

    try:
        source = output / "source"
        run(
            "checkout",
            [
                "git",
                "clone",
                "--local",
                "--no-hardlinks",
                "--no-checkout",
                original,
                source,
            ],
            output,
        )
        run("revision", ["git", "checkout", "--detach", revision], source)
        run(
            "python-locked-environment",
            [
                "uv",
                "sync",
                "--all-packages",
                "--locked",
                "--extra",
                "dev",
                "--extra",
                "contracts",
                "--python",
                sys.executable,
            ],
            source,
        )
        python = output / "venv/bin/python"
        assets = source / "contracts"
        run("python-lint", [python, "-m", "ruff", "check", "."], source)
        run("python-format", [python, "-m", "ruff", "format", "--check", "."], source)
        run("python-types", [python, "-m", "pyrefly", "check"], source)
        run(
            "callback-isolation",
            [python, "-m", "contracts.deploy.checkisolation"],
            source,
        )
        for name in ("header-spikes", "room-sim", "contract-lab"):
            project = assets / name
            run(name + "-dependencies", ["mix", "deps.get", "--check-locked"], project)
            run(name + "-format", ["mix", "format", "--check-formatted"], project)
            run(name + "-compile", ["mix", "compile", "--warnings-as-errors"], project)
            run(name + "-tests", ["mix", "test"], project)
        # The development Python transport deliberately loads _build/dev.
        environment["MIX_ENV"] = "dev"
        run(
            "python-engine-dependencies",
            ["mix", "deps.get", "--check-locked"],
            assets / "contract-lab",
        )
        run(
            "python-engine-compile",
            ["mix", "compile", "--warnings-as-errors"],
            assets / "contract-lab",
        )
        band_engine = assets / "engines/band-v1/contract-lab"
        run(
            "band-python-engine-dependencies",
            ["mix", "deps.get", "--check-locked"],
            band_engine,
        )
        run(
            "band-python-engine-compile",
            ["mix", "compile", "--warnings-as-errors"],
            band_engine,
        )
        run(
            "python-runtime",
            [
                python,
                "-W",
                "error",
                "-m",
                "pytest",
                "-p",
                "no:looptime_plugin",
                "tests/contracts/runtime",
                "--contract-qualification",
                "--no-cov",
            ],
            source,
        )
        run(
            "release-preconditions",
            [
                python,
                "-W",
                "error",
                "-m",
                "pytest",
                "-p",
                "no:looptime_plugin",
                "tests/contracts/release",
                "--contract-qualification",
                "--no-cov",
            ],
            source,
        )
        run(
            "contract-sdk-api",
            [
                python,
                "-m",
                "pytest",
                "tests/contracts/author",
                "tests/contracts/client",
                "tests/contracts/lifecycle",
                "tests/contracts/programs",
                "tests/contracts/tools",
                "--contract-qualification",
                "--no-cov",
            ],
            source,
        )
        run(
            "portable-specification",
            [python, "-m", "contracts.spec.tools.checkspec"],
            source,
        )
        run("v2-specification", [python, "-m", "contracts.spec.tools.checkv2"], source)
        run(
            "coordination-specification",
            [
                python,
                "-m",
                "contracts.deploy.coordinationgate",
                "--output",
                output / "coordination-report.json",
            ],
            source,
        )
        run(
            "http-specification",
            [
                python,
                "-m",
                "contracts.spec.tools.checkhttp",
                "--output",
                output / "http-report.json",
            ],
            source,
        )
        run(
            "specification-checker",
            [
                python,
                "-W",
                "error",
                "-m",
                "pytest",
                "-p",
                "no:looptime_plugin",
                "tests/contracts/spec",
                "--contract-qualification",
                "--no-cov",
            ],
            source,
        )
        run("sdk-unit", [python, "-m", "pytest", "--ignore=tests/contracts"], source)
        for name in ("release-a", "release-b"):
            run(
                name,
                [python, "-m", "contracts.deploy.buildrelease", output / name],
                source,
            )
        first = verify_release(output / "release-a").manifest
        second = verify_release(output / "release-b").manifest
        if (
            first != second
            or first["source_commit"] != revision
            or first["source_dirty"]
        ):
            raise RuntimeError(
                "Release manifests are not identical clean builds of the selected commit"
            )
        for name in ("release-a", "release-b"):
            release = output / name
            for path in [release, *release.rglob("*")]:
                if (
                    path.is_symlink()
                    or int(path.stat().st_mtime) != first["build"]["source_date_epoch"]
                ):
                    raise RuntimeError("Nonreproducible release metadata: " + str(path))
        result["reproducibility"] = {
            "identical_manifests": True,
            "file_count": len(first["files"]),
            "manifest_sha256": hashlib.sha256(
                (output / "release-a/manifest.json").read_bytes()
            ).hexdigest(),
        }
        run(
            "installed-release",
            [python, "-m", "contracts.deploy.smokerelease", output / "release-a"],
            source,
        )
        result["status"] = "passed"
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        raise
    finally:
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
