"""Opt-in temporary credential custody from a workspace secrets file.

Disabled unless `band.yaml` selects `source: workspace-env-file` AND
acknowledges that plaintext keys exist in both the host workspace and the
sandbox VM. `SecretsFile.locate` guarantees the configured path stays inside
the workspace; every other safeguard is one named guard in `GUARDS` — the
tuple below *is* the security policy, in the order it is enforced. Values
already present in the process environment always win (the file only fills
gaps) and are never logged.
"""

from __future__ import annotations

import logging
import stat
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from typing import Callable

from dotenv import dotenv_values

from band.docker.launcher.config import (
    CredentialsSection,
    LauncherEnv,
    WorkspaceConfig,
)
from band.docker.launcher.errors import LaunchError
from band.docker.launcher.paths import resolve_inside

logger = logging.getLogger(__name__)


class CredentialName(StrEnum):
    """The only names the secrets file may define.

    Anything else is rejected, never silently dropped, so a typo'd or
    smuggled variable is caught before customer code runs.
    """

    BAND_API_KEY = "BAND_API_KEY"
    OPENAI_API_KEY = "OPENAI_API_KEY"
    ANTHROPIC_API_KEY = "ANTHROPIC_API_KEY"
    COPILOT_GITHUB_TOKEN = "COPILOT_GITHUB_TOKEN"
    GH_TOKEN = "GH_TOKEN"
    GITHUB_TOKEN = "GITHUB_TOKEN"


@dataclass
class SecretsFile:
    """The opt-in plaintext secrets file inside the customer workspace."""

    raw_path: Path  # as configured, before symlink resolution
    path: Path  # fully resolved
    workspace: Path

    @classmethod
    def locate(cls, workspace: Path, configured: str) -> SecretsFile:
        """Resolve the configured path, guaranteeing workspace containment."""
        resolved = resolve_inside(
            workspace, configured, name="credentials file", phase="credentials"
        )
        raw = Path(configured)
        return cls(
            raw_path=raw if raw.is_absolute() else workspace / raw,
            path=resolved,
            workspace=workspace,
        )

    @cached_property
    def values(self) -> dict[str, str]:
        """Parsed name -> value pairs (dotenv: no execution, no sourcing)."""
        return {
            key: value
            for key, value in dotenv_values(self.path).items()
            if value is not None
        }


def never_traverses_a_symlink(secrets: SecretsFile) -> None:
    """A link anywhere below the workspace is an unexpected indirection."""
    for candidate in [secrets.raw_path, *secrets.raw_path.parents]:
        if candidate == secrets.workspace:
            return
        if candidate.is_symlink():
            raise LaunchError(
                "credentials",
                f"credentials file path must not traverse a symlink: {candidate}",
            )


def is_a_file(secrets: SecretsFile) -> None:
    if not secrets.path.is_file():
        raise LaunchError("credentials", f"credentials file not found: {secrets.path}")


def has_owner_only_permissions(secrets: SecretsFile) -> None:
    """Group/other access to plaintext keys is always a mistake."""
    mode = stat.S_IMODE(secrets.path.stat().st_mode)
    if mode & 0o077:
        raise LaunchError(
            "credentials",
            f"credentials file must be owner-only (e.g. 600), got {mode:o}: "
            f"{secrets.path}",
        )


def run_git(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git against the workspace, tolerating a foreign-uid bind mount.

    `-c safe.directory=<workspace>`: a bind-mounted workspace is often owned
    by a different uid than the agent user, which trips Git's dubious-
    ownership guard and would otherwise make every git call here fail.
    """
    command = ["git", "-c", f"safe.directory={workspace}", "-C", str(workspace), *args]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def require_never_tracked(secrets: SecretsFile) -> None:
    """A committed secrets file leaks with every clone."""
    tracked = run_git(
        secrets.workspace, "ls-files", "--error-unmatch", str(secrets.path)
    )
    if tracked.returncode == 0:
        raise LaunchError(
            "credentials",
            f"credentials file is tracked by Git — never commit it: {secrets.path}",
        )
    if tracked.returncode != 1:
        raise LaunchError(
            "credentials",
            "git could not determine the credentials file's tracked state: "
            + (tracked.stderr or "").strip(),
        )


def require_gitignored(secrets: SecretsFile) -> None:
    """An unignored secrets file is one `git add .` away from a leak."""
    ignored = run_git(secrets.workspace, "check-ignore", "-q", str(secrets.path))
    if ignored.returncode == 1:
        raise LaunchError(
            "credentials",
            f"credentials file must be gitignored: {secrets.path}",
        )
    if ignored.returncode != 0:
        raise LaunchError(
            "credentials",
            "git could not determine the credentials file's ignore state: "
            + (ignored.stderr or "").strip(),
        )


def is_gitignored_and_never_tracked(secrets: SecretsFile) -> None:
    """Outside a Git repository there is no tracking risk and the check is
    skipped. Inside one, any unexpected git failure fails the launch
    (closed) — this guard must never be silently disabled."""
    inside = run_git(secrets.workspace, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0:
        logger.warning(
            "Workspace is not a git repository; skipping gitignore check for "
            "the credentials file"
        )
        return
    require_never_tracked(secrets)
    require_gitignored(secrets)


def defines_only_documented_names(secrets: SecretsFile) -> None:
    unknown = sorted(set(secrets.values) - {name.value for name in CredentialName})
    if unknown:
        raise LaunchError(
            "credentials",
            "credentials file defines undocumented names: " + ", ".join(unknown),
        )


# The security policy, in enforcement order. Containment (the file must stay
# inside the workspace) is guaranteed earlier, by SecretsFile.locate.
GUARDS: tuple[Callable[[SecretsFile], None], ...] = (
    never_traverses_a_symlink,
    is_a_file,
    has_owner_only_permissions,
    is_gitignored_and_never_tracked,
    defines_only_documented_names,
)


def require_explicit_opt_in(section: CredentialsSection) -> None:
    """Plaintext custody is never implicit: supported source + acknowledgement."""
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


def guarded_secrets_file(workspace: Path, configured: str) -> SecretsFile:
    """Locate the file and enforce every guard before its values are used."""
    secrets = SecretsFile.locate(workspace, configured)
    for guard in GUARDS:
        try:
            guard(secrets)
        except OSError as exc:
            # e.g. a host-uid-owned 600 file is unreadable to the agent user —
            # keep the failure attributed to its phase.
            raise LaunchError(
                "credentials", f"credentials file is not readable: {exc}"
            ) from exc
    return secrets


def missing_from_environment(secrets: SecretsFile, env: LauncherEnv) -> dict[str, str]:
    """Existing process environment always wins; the file only fills gaps."""
    env_values = env.model_dump()
    return {
        name: value
        for name, value in secrets.values.items()
        if not env_values.get(name.lower())
    }


def load_file_credentials(
    config: WorkspaceConfig, env: LauncherEnv, workspace: Path
) -> dict[str, str]:
    """Return name -> value for documented credentials missing from the env."""
    section = config.credentials
    if section is None:
        return {}
    require_explicit_opt_in(section)
    secrets = guarded_secrets_file(workspace, section.path)
    resolved = missing_from_environment(secrets, env)
    logger.info(
        "Loaded %d credential value(s) from the workspace env file", len(resolved)
    )
    return resolved
