"""
Preflight checks for the sandbox staging smoke: tools, environment, and
non-production guardrails — reported without ever printing secret values.

Reuses `probe.py`'s settings loader (repo-root import, production-URL guard)
rather than re-implementing it; see `probe.py`'s docstring for why both it
and this script reach into `tests/e2e/baseline`. `setup.sh` runs this script
as its own first step rather than duplicating any of these checks itself.

Exit code 0 = every check passed; 1 = at least one failed (reason on stderr,
no printed secrets).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

import root  # noqa: F401  (bootstraps sys.path as a side effect)

import probe
import state

# No basicConfig call here: importing `probe` above already configured the
# root logger (stderr, "%(asctime)s %(levelname)s %(name)s: %(message)s") —
# a second basicConfig call is a silent no-op once handlers exist, so this
# reuses that configuration rather than pretending to set its own.
logger = logging.getLogger(__name__)


class SandboxSettings(BaseSettings):
    """The `sbx`-specific config this smoke needs, read the same way
    `BaselineSettings` reads Band platform config — not a directory-scoped
    `.env`; by the time this constructs, `tests.e2e.baseline.settings` (via
    the `probe` import above) has already loaded the repo root's `.env.test`.
    """

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    sbx_sandbox: str = ""  # SBX_SANDBOX
    sbx_workspace: str = ""  # SBX_WORKSPACE


def check(name: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    suffix = f" — {detail}" if detail else ""
    logger.info("[%s] %s%s", status, name, suffix)
    return ok


def main() -> int:
    results: list[bool] = []

    sbx_path = shutil.which("sbx")
    results.append(check("sbx installed", sbx_path is not None))
    if sbx_path:
        version_lines = subprocess.run(
            ["sbx", "version"], capture_output=True, text=True, check=False
        ).stdout.splitlines()
        results.append(
            check(
                "sbx version reported",
                bool(version_lines),
                version_lines[0] if version_lines else "",
            )
        )

    sandbox = SandboxSettings()
    results.append(check("SBX_SANDBOX set", bool(sandbox.sbx_sandbox)))
    results.append(check("SBX_WORKSPACE set", bool(sandbox.sbx_workspace)))
    if sandbox.sbx_workspace:
        inside_repo = (
            Path(sandbox.sbx_workspace)
            .expanduser()
            .resolve()
            .is_relative_to(state.repo_root())
        )
        results.append(check("SBX_WORKSPACE is outside this checkout", not inside_repo))

    try:
        probe.load_settings()
        results.append(
            check(
                "Staging endpoints + BAND_API_KEY_USER present and non-production",
                True,
            )
        )
    except ValueError as error:
        results.append(
            check(
                "Staging endpoints + BAND_API_KEY_USER present and non-production",
                False,
                str(error),
            )
        )

    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
