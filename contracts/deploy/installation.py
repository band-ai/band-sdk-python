"""Install the canonical SDK and its locked dependencies from a sealed release."""

from __future__ import annotations

import subprocess
from pathlib import Path

from contracts.deploy.releaseverification import VerifiedRelease, verify_release


def installation_commands(
    release: VerifiedRelease, python: Path, *, force: bool = False
) -> tuple[tuple[str, ...], ...]:
    """Build local-only installation commands after verifying the complete inventory."""
    if "requirements-runtime.lock" not in release.manifest["files"]:
        raise ValueError("Release requires its sealed runtime dependency lock")
    pip = (str(python), "-I", "-m", "pip", "--isolated", "--disable-pip-version-check")
    install = (
        *pip,
        "install",
        "--no-index",
        *(("--force-reinstall",) if force else ()),
    )
    return (
        (
            *install,
            "--require-hashes",
            "--only-binary=:all:",
            "--find-links",
            str(release.root / "dependencies"),
            "-r",
            str(release.root / "requirements-runtime.lock"),
        ),
        (*install, "--no-deps", str(release.wheel)),
        (*pip, "check"),
    )


def install_release(source: Path, python: Path, *, force: bool = False) -> None:
    """Install locked dependency wheels before the SDK, then verify consistency."""
    release = verify_release(source)
    for command in installation_commands(release, python, force=force):
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
