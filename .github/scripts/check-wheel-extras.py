"""Verify clean wheel installs, provider startup, and incompatible-extra rejection.

Run outside the repository resolver: each case gets a fresh virtual environment
and installs the built wheel through its published Requires-Dist metadata.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from wheelchecks import CONSUMERS, Consumer, Framework

logger = logging.getLogger(__name__)
PROVIDER_PROBE = Path(__file__).with_name("check-provider-startup.py")


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, cwd=cwd, text=True, capture_output=True, check=False
    )
    logger.info("$ %s\n%s%s", " ".join(command), result.stdout, result.stderr)
    return result


def require_success(command: list[str], cwd: Path) -> None:
    result = run(command, cwd)
    if result.returncode != 0:
        raise RuntimeError(f"Wheel consumer check failed: {command}")


def install_consumer(wheel: Path, consumer: Consumer, python: Path, cwd: Path) -> None:
    requirements = [f"{wheel}[{consumer.extras}]"]
    if consumer.openai_major == 3:
        requirements.append("openai>=3.26,<4")
    require_success(
        ["uv", "pip", "install", "--python", str(python), *requirements], cwd
    )
    require_success(["uv", "pip", "check", "--python", str(python)], cwd)


def check_providers(consumer: Consumer, python: Path, cwd: Path) -> None:
    framework_flags = [
        value
        for framework in consumer.frameworks
        for value in ("--framework", str(framework))
    ]
    require_success(
        [
            str(python),
            str(PROVIDER_PROBE),
            "--openai-major",
            str(consumer.openai_major),
            *framework_flags,
        ],
        cwd,
    )


def check_incompatible_extras(
    wheel: Path, consumer: Consumer, python: Path, cwd: Path
) -> None:
    extras = f"{Framework.PYDANTIC_AI},{consumer.extras}"
    result = run(
        ["uv", "pip", "install", "--python", str(python), f"{wheel}[{extras}]"], cwd
    )
    if result.returncode == 0 or "No solution found" not in result.stderr:
        raise RuntimeError("Incompatible extras did not fail dependency resolution")
    logger.info("Incompatible extras rejected: %s", extras)


def check_consumer(wheel: Path, consumer: Consumer) -> None:
    with tempfile.TemporaryDirectory(prefix="band-wheel-") as directory:
        cwd = Path(directory)
        environment = cwd / "venv"
        require_success(["uv", "venv", "--python", "3.12", str(environment)], cwd)
        python = environment / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        install_consumer(wheel, consumer, python, cwd)
        check_providers(consumer, python, cwd)
        if consumer.rejects_pydantic_ai:
            check_incompatible_extras(wheel, consumer, python, cwd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--case", choices=CONSUMERS, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    check_consumer(args.wheel.resolve(), CONSUMERS[args.case])


if __name__ == "__main__":
    main()
