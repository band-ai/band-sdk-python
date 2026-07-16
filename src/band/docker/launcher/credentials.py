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

    configured = env.band_kit_credentials_path or section.path
    cred_path = resolve_inside(
        workspace, configured, name="credentials file", phase="credentials"
    )
    # The pre-resolve path must not itself be a symlink even when its target
    # stays inside the workspace: a link is an unexpected indirection for a
    # secrets file.
    raw_path = (
        Path(configured) if Path(configured).is_absolute() else workspace / configured
    )
    if raw_path.is_symlink():
        raise LaunchError(
            "credentials", f"credentials file must not be a symlink: {raw_path}"
        )
    if not cred_path.is_file():
        raise LaunchError("credentials", f"credentials file not found: {cred_path}")

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
    """Reject a Git-tracked credentials file; require it to be gitignored."""
    # `-c safe.directory=<workspace>`: a bind-mounted workspace is often owned
    # by a different uid than the agent user, which trips Git's dubious-
    # ownership guard (also exit 128) and would silently disable both checks.
    git = ["git", "-c", f"safe.directory={workspace}", "-C", str(workspace)]
    tracked = subprocess.run(
        [*git, "ls-files", "--error-unmatch", str(cred_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if tracked.returncode == 0:
        raise LaunchError(
            "credentials",
            f"credentials file is tracked by Git — never commit it: {cred_path}",
        )

    ignored = subprocess.run(
        [*git, "check-ignore", "-q", str(cred_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if ignored.returncode == 1:
        # 1 = definitively not ignored (and, from above, not tracked either).
        raise LaunchError(
            "credentials",
            f"credentials file must be gitignored: {cred_path}",
        )
    if ignored.returncode not in (0, 1):
        # 128 = not a git repository: no tracking risk exists, allow it.
        logger.warning(
            "Workspace is not a git repository; skipping gitignore check for "
            "the credentials file"
        )
