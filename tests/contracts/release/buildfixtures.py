"""Build canonical source and sealed-release fixtures for the release gates."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

from contracts.deploy import ci
from contracts.deploy.environment import BuildSettings
from tests.contracts.release.kit import release, seal, wheel
from tests.paths import CONTRACT_DEPLOY_ROOT, CONTRACTS_ROOT

CHECKERS = {
    "checkv2.py": "v2-specification",
    "checkcoordination.py": "coordination-specification",
    "checkhttp.py": "http-specification",
}
DEPLOY_MODULES = (
    "__init__.py",
    "buildrelease.py",
    "checkbandengine.py",
    "ci.py",
    "coordinationgate.py",
    "environment.py",
    "releaseverification.py",
    "toolchain.py",
)
EPOCH = 1


def command_environment(bin_paths: list[str] | None = None) -> dict[str, str]:
    """Keep fixture children free of ambient credentials and bytecode writes."""
    environment = BuildSettings().environment(bin_paths or [], mix_env="test")
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return environment


def copy_build_helpers(root: Path) -> None:
    """Copy the current entrypoints with their complete canonical import closure."""
    deploy = root / "contracts/deploy"
    deploy.mkdir(parents=True)
    shutil.copy2(CONTRACTS_ROOT / "__init__.py", root / "contracts/__init__.py")
    for name in DEPLOY_MODULES:
        shutil.copy2(CONTRACT_DEPLOY_ROOT / name, deploy / name)


def commit_fixture(root: Path) -> str:
    """Create one local fixture commit with signing and hooks disabled."""
    environment = command_environment()
    for arguments in (
        ["init", "-q"],
        ["add", "."],
        [
            "-c",
            "user.name=Release fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=" + os.devnull,
            "commit",
            "-qm",
            "fixture",
        ],
    ):
        subprocess.run(
            ["git", *arguments],
            cwd=root,
            env=environment,
            check=True,
            capture_output=True,
            timeout=30,
        )
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        env=environment,
        text=True,
        timeout=30,
    ).strip()


def build_source(root: Path) -> Path:
    """Create a committed build source suitable for precondition refusals."""
    copy_build_helpers(root)
    (root / "tracked.txt").write_text("committed\n", encoding="utf-8")
    commit_fixture(root)
    return root


def unpatched_toolchain(directory: Path) -> dict[str, str]:
    """Provide a real executable that reports the unsupported OTP 27.0 version."""
    directory.mkdir()
    executable = directory / "elixir"
    executable.write_text(
        "#!" + sys.executable + "\n"
        "import sys\n"
        "sys.stdout.write('/unused/elixir\\n/unused/erlang\\n1.18.4\\n27.0\\n')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return command_environment([str(directory)])


def ci_source(root: Path, broken: str | None) -> str:
    """Create real checker modules at the SDK's canonical source paths."""
    copy_build_helpers(root)
    tools = root / "contracts/spec/tools"
    tools.mkdir(parents=True)
    for package in ("spec", "spec/tools"):
        shutil.copy2(
            CONTRACTS_ROOT / package / "__init__.py",
            root / "contracts" / package / "__init__.py",
        )
    conformance = root / "contracts/spec/conformance"
    conformance.mkdir()
    (conformance / "coordination-report.json").write_text(
        "tracked original\n", encoding="utf-8"
    )
    for project in (
        "header-spikes",
        "room-sim",
        "contract-lab",
        "engines/band-v1/contract-lab",
    ):
        directory = root / "contracts" / project
        directory.mkdir(parents=True)
        (directory / ".fixture").write_text("native boundary\n", encoding="utf-8")
    for name in ("checkspec.py", *CHECKERS):
        body = "import sys\n"
        if name == "checkcoordination.py":
            body += (
                "from pathlib import Path\n"
                "source = Path(__file__).resolve().parents[2]\n"
                "(source / 'spec/conformance/coordination-report.json').write_text("
                "'{\"passed\": true}\\n', encoding='utf-8')\n"
            )
        body += f"raise SystemExit({37 if name == broken else 0})\n"
        (tools / name).write_text(body, encoding="utf-8")
    return commit_fixture(root)


def sealed_ci_release(root: Path, revision: str) -> None:
    """Seal actual SDK and dependency wheels with the CI source identity."""
    release(root)
    dependency = wheel(
        root / "dependencies/contract_probe-6.0.1-py3-none-any.whl",
        "contract-probe",
        modules={"contract_probe.py": "VALUE = 'sealed fixture dependency'\n"},
    )
    wheel(
        root / "wheels/band_sdk-6.0.1-py3-none-any.whl",
        requirements=("contract-probe==6.0.1",),
        modules={"band_probe.py": "from contract_probe import VALUE\n"},
    )
    digest = hashlib.sha256(dependency.read_bytes()).hexdigest()
    (root / "requirements-runtime.lock").write_text(
        f"contract-probe==6.0.1 --hash=sha256:{digest}\n", encoding="utf-8"
    )
    seal(root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(source_commit=revision, build={"source_date_epoch": EPOCH})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    for path in [root, *root.rglob("*")]:
        os.utime(path, (EPOCH, EPOCH))


class CIHarness:
    """Mock build infrastructure while retaining actual checker child processes."""

    def __init__(
        self,
        directory: Path,
        *,
        broken: str | None = None,
        broken_band_engine: bool = False,
        broken_isolation: bool = False,
    ) -> None:
        self.root = directory / "fixture"
        self.revision = ci_source(self.root, broken)
        self.output = directory / "ci-output"
        self.broken_band_engine = broken_band_engine
        self.broken_isolation = broken_isolation
        self.run_child = subprocess.run

    def infrastructure(
        self, command: Sequence[str | Path], **kwargs: Any
    ) -> subprocess.CompletedProcess[Any]:
        argv = [str(argument) for argument in command]
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = 30
        if argv[0] == "git":
            kwargs["env"] = command_environment()
            return self.run_child(argv, **kwargs)
        if argv[:2] == ["uv", "sync"]:
            python = Path(kwargs["env"]["UV_PROJECT_ENVIRONMENT"]) / "bin/python"
            python.parent.mkdir(parents=True)
            python.symlink_to(sys.executable)
            return subprocess.CompletedProcess(argv, 0)
        if argv[0] == "mix":
            if (
                self.broken_band_engine
                and argv[1] == "compile"
                and Path(kwargs["cwd"])
                == self.output / "source/contracts/engines/band-v1/contract-lab"
            ):
                return self.run_child(
                    [sys.executable, "-c", "raise SystemExit(37)"], **kwargs
                )
            return subprocess.CompletedProcess(argv, 0)
        module = argv[argv.index("-m") + 1] if "-m" in argv else None
        match module:
            case (
                "contracts.spec.tools.checkspec"
                | "contracts.spec.tools.checkv2"
                | "contracts.spec.tools.checkhttp"
                | "contracts.deploy.coordinationgate"
            ):
                return self.run_child(argv, **kwargs)
            case "contracts.deploy.checkisolation":
                if self.broken_isolation:
                    return self.run_child(
                        [sys.executable, "-c", "raise SystemExit(37)"], **kwargs
                    )
            case "contracts.deploy.buildrelease":
                sealed_ci_release(Path(argv[-1]), self.revision)
            case "ruff" | "pyrefly" | "pytest" | "contracts.deploy.smokerelease":
                pass
            case _:
                raise AssertionError(f"Unexpected CI fixture command: {argv}")
        return subprocess.CompletedProcess(argv, 0)

    def exercise(self) -> tuple[Path, dict[str, Any], RuntimeError | None]:
        failure: RuntimeError | None = None
        with (
            patch.object(
                sys,
                "argv",
                ["ci", str(self.output), "--source-root", str(self.root)],
            ),
            patch.object(
                ci,
                "select",
                return_value={
                    "elixir": "1.18.4",
                    "erlang": "27.3.4.17",
                    "bin_paths": [],
                },
            ),
            patch.object(ci.subprocess, "run", side_effect=self.infrastructure),
        ):
            try:
                ci.main()
            except RuntimeError as error:
                failure = error
        return (
            self.output,
            json.loads((self.output / "result.json").read_text(encoding="utf-8")),
            failure,
        )
