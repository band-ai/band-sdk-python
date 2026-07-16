"""Opt-in temporary credential custody from a workspace env file.

Disabled unless `band.yaml` selects `source: workspace-env-file` AND
acknowledges that plaintext keys exist in both the host workspace and the
sandbox VM. The file must be gitignored (never Git-tracked), owner-only,
not a symlink, inside the workspace, and may only define documented
credential names. Values already present in the process environment always
win — the file only fills gaps. Values are never logged.
"""

from __future__ import annotations

import logging
import stat
import subprocess
from pathlib import Path

from dotenv import dotenv_values

from band.docker.launcher.config import LauncherEnv, WorkspaceConfig
from band.docker.launcher.errors import LaunchError
from band.docker.launcher.paths import resolve_inside

logger = logging.getLogger(__name__)

# The only names the credential file may define. Names outside this set are
# rejected, never silently dropped, so a typo'd or smuggled variable is
# caught before customer code runs.
CREDENTIAL_ENV_NAMES = frozenset(
    {
        "BAND_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "COPILOT_GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    }
)


def load_file_credentials(
    config: WorkspaceConfig, env: LauncherEnv, workspace: Path
) -> dict[str, str]:
    """Return name -> value for documented credentials missing from the env."""
    section = config.credentials
    if section is None:
        return {}
    if section.source != "workspace-env-file":
        raise LaunchError(
            "credentials",
            f"unsupported credentials.source: {section.source!r} "
            "(only 'workspace-env-file' is supported)",
        )
    if not section.acknowledge_plaintext_in_sandbox:
        raise LaunchError(
            "credentials",
            "credentials.acknowledgePlaintextInSandbox: true is required — the "
            "workspace env file places plaintext keys in both the host "
            "workspace and the sandbox VM",
        )

    configured = section.path
    cred_path = resolve_inside(
        workspace, configured, name="credentials file", phase="credentials"
    )
    # No component of the pre-resolve path below the workspace may be a
    # symlink, even when the target stays inside the workspace: a link is an
    # unexpected indirection for a secrets file.
    raw_path = (
        Path(configured) if Path(configured).is_absolute() else workspace / configured
    )
    for candidate in [raw_path, *raw_path.parents]:
        if candidate == workspace:
            break
        if candidate.is_symlink():
            raise LaunchError(
                "credentials",
                f"credentials file path must not traverse a symlink: {candidate}",
            )
    if not cred_path.is_file():
        raise LaunchError("credentials", f"credentials file not found: {cred_path}")

    try:
        mode = stat.S_IMODE(cred_path.stat().st_mode)
        if mode & 0o077:
            raise LaunchError(
                "credentials",
                f"credentials file must be owner-only (e.g. 600), got {mode:o}: "
                f"{cred_path}",
            )
        _require_gitignored(cred_path, workspace)

        # dotenv_values parses without executing or shell-sourcing the file.
        parsed = {
            key: value
            for key, value in dotenv_values(cred_path).items()
            if value is not None
        }
    except OSError as exc:
        # e.g. a host-uid-owned 600 file is unreadable to the agent user —
        # keep the failure attributed to its phase.
        raise LaunchError(
            "credentials", f"credentials file is not readable: {exc}"
        ) from exc
    unknown = sorted(set(parsed) - CREDENTIAL_ENV_NAMES)
    if unknown:
        raise LaunchError(
            "credentials",
            "credentials file defines undocumented names: " + ", ".join(unknown),
        )

    env_values = env.model_dump()
    resolved = {
        name: value
        for name, value in parsed.items()
        # Existing process environment always wins; the file only fills gaps.
        if not env_values.get(name.lower())
    }
    logger.info(
        "Loaded %d credential value(s) from the workspace env file", len(resolved)
    )
    return resolved


def _require_gitignored(cred_path: Path, workspace: Path) -> None:
    """Reject a Git-tracked credentials file; require it to be gitignored.

    Classifies the workspace first: outside a Git repository there is no
    tracking risk and the check is skipped. Inside one, any unexpected git
    failure fails the launch (closed) — a guard on plaintext secrets must
    never be silently disabled by an unclassified error.
    """
    # `-c safe.directory=<workspace>`: a bind-mounted workspace is often owned
    # by a different uid than the agent user, which trips Git's dubious-
    # ownership guard and would otherwise make every call below fail.
    git = ["git", "-c", f"safe.directory={workspace}", "-C", str(workspace)]

    def run_git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*git, *args], capture_output=True, text=True, check=False
        )

    inside = run_git("rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0:
        logger.warning(
            "Workspace is not a git repository; skipping gitignore check for "
            "the credentials file"
        )
        return

    tracked = run_git("ls-files", "--error-unmatch", str(cred_path))
    if tracked.returncode == 0:
        raise LaunchError(
            "credentials",
            f"credentials file is tracked by Git — never commit it: {cred_path}",
        )
    if tracked.returncode != 1:
        raise LaunchError(
            "credentials",
            "git could not determine the credentials file's tracked state: "
            + (tracked.stderr or "").strip(),
        )

    ignored = run_git("check-ignore", "-q", str(cred_path))
    if ignored.returncode == 1:
        raise LaunchError(
            "credentials",
            f"credentials file must be gitignored: {cred_path}",
        )
    if ignored.returncode != 0:
        raise LaunchError(
            "credentials",
            "git could not determine the credentials file's ignore state: "
            + (ignored.stderr or "").strip(),
        )
