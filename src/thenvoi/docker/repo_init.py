"""Repository initialization utilities for Docker runners.

Features:
- Optional repo bootstrap from agent config.
- SSH/HTTPS remote support with auth preflight checks.
- Concurrency-safe init via file lock for multi-agent startup.
- Optional context indexing to `/workspace/context`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import time
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generator
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError, field_validator

logger = logging.getLogger(__name__)

DEFAULT_STATE_DIR = Path("/workspace/state")
DEFAULT_CONTEXT_DIR = Path("/workspace/context")
DEFAULT_LOCK_TIMEOUT_S = 120.0
CONTEXT_FILENAMES = ("structure.md", "patterns.md", "dependencies.md")
_SSH_STRICT_ENV = "GIT_SSH_STRICT_HOST_KEY_CHECKING"


class RepoConfig(BaseModel):
    """Repository initialization configuration.

    When ``url`` is provided, the repository is cloned (if missing) and
    remote validation is performed.  When ``url`` is omitted (local-only
    mode), ``path`` must point to an existing directory — no clone is
    attempted and auth preflight is skipped.
    """

    url: str | None = None
    path: str
    branch: str | None = None
    index: bool = False

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        val = value.strip()
        if not val:
            return None
        if not (is_ssh_url(val) or is_https_url(val)):
            raise ValueError(
                "repo.url must be SSH (git@... or ssh://...) or HTTPS (https://...)"
            )
        return val

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        val = value.strip()
        if not val:
            raise ValueError("repo.path must not be empty")
        if not Path(val).is_absolute():
            raise ValueError("repo.path must be an absolute path")
        return val

    @field_validator("branch")
    @classmethod
    def _validate_branch(cls, value: str | None) -> str | None:
        if value is None:
            return None
        val = value.strip()
        return val or None


class RepoInitMetadata(BaseModel):
    """Persisted repo initialization metadata."""

    repo_url: str = ""
    repo_path: str
    branch: str | None = None
    head_commit: str
    index_enabled: bool
    indexed_at: str | None = None
    context_files: dict[str, str]


class RepoInitResult(BaseModel):
    """Result returned by repo initialization."""

    enabled: bool = False
    repo_path: str | None = None
    cloned: bool = False
    indexed: bool = False
    context_bundle: str = ""


def is_ssh_url(url: str) -> bool:
    """Return True when a git remote URL uses SSH."""
    return url.startswith("git@") or url.startswith("ssh://")


def is_https_url(url: str) -> bool:
    """Return True when a git remote URL uses HTTPS."""
    return url.startswith("https://")


def parse_repo_config(config: dict[str, Any]) -> RepoConfig | None:
    """Parse and validate optional repo config from an agent section."""
    repo = config.get("repo")
    if repo is None:
        return None
    if not isinstance(repo, dict):
        raise ValueError("repo must be a mapping when provided")
    try:
        return RepoConfig.model_validate(repo)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        raise ValueError(f"Invalid repo configuration: {details}") from exc


def initialize_repo(
    config: dict[str, Any],
    *,
    agent_key: str,
    state_dir: Path = DEFAULT_STATE_DIR,
    context_dir: Path = DEFAULT_CONTEXT_DIR,
    lock_timeout_s: float = DEFAULT_LOCK_TIMEOUT_S,
) -> RepoInitResult:
    """Initialize repository and optional context docs from config.

    When ``repo.url`` is set, the repository is cloned (if missing) with auth
    checks.  When ``repo.url`` is omitted (local-only mode), the path is
    validated to exist and no clone is performed.
    """
    repo = parse_repo_config(config)
    if repo is None:
        return RepoInitResult(enabled=False)

    repo_path = Path(repo.path)
    state_dir.mkdir(parents=True, exist_ok=True)
    context_dir.mkdir(parents=True, exist_ok=True)

    lock_path = state_dir / "repo_init.lock"
    meta_path = state_dir / "repo_init_meta.json"

    logger.info(
        "Repo init enabled for %s: url=%s path=%s branch=%s index=%s",
        agent_key,
        repo.url or "(local)",
        repo.path,
        repo.branch or "default",
        repo.index,
    )

    with _locked_file(lock_path, timeout_s=lock_timeout_s):
        if repo.url:
            # Remote mode: preflight auth, clone if needed
            _preflight_repo_auth(repo.url)
            cloned = _ensure_repo(repo, repo_path)
        else:
            # Local-only mode: validate path exists, no clone
            cloned = False
            _ensure_local_path(repo_path)
            if repo.branch:
                _ensure_branch(repo, repo_path)

        head_commit = _git(repo_path, "rev-parse", "HEAD").strip()

        metadata = _read_metadata(meta_path)
        indexed = False
        if repo.index and _should_reindex(repo, metadata, head_commit, context_dir):
            _generate_context(repo_path, context_dir)
            indexed = True
            logger.info("Generated repository context files at %s", context_dir)

        _write_metadata(
            meta_path,
            RepoInitMetadata(
                repo_url=repo.url or "",
                repo_path=repo.path,
                branch=repo.branch,
                head_commit=head_commit,
                index_enabled=repo.index,
                indexed_at=(
                    datetime.now(UTC).isoformat().replace("+00:00", "Z")
                    if repo.index
                    else None
                ),
                context_files=_hash_context_files(context_dir),
            ),
        )

    return RepoInitResult(
        enabled=True,
        repo_path=repo.path,
        cloned=cloned,
        indexed=indexed,
        context_bundle=load_context_bundle(context_dir),
    )


def load_context_bundle(context_dir: Path = DEFAULT_CONTEXT_DIR) -> str:
    """Load generated context docs and format for system prompts."""
    sections: list[str] = []
    for name in CONTEXT_FILENAMES:
        file_path = context_dir / name
        if not file_path.exists():
            continue
        content = file_path.read_text(encoding="utf-8").strip()
        if not content:
            continue
        sections.append(f"### {name}\n\n{content}")

    if not sections:
        return ""

    rendered_sections = "\n\n".join(sections)
    return (
        "## Repository Context\n\n"
        "The following context files were generated from the working repository:\n\n"
        f"{rendered_sections}"
    )


def _ensure_local_path(repo_path: Path) -> None:
    """Validate that a local-only repo path exists as a directory."""
    if not repo_path.exists():
        raise ValueError(
            f"repo.path does not exist: {repo_path}. "
            "For local-only mode (no repo.url), the directory must already exist."
        )
    if not repo_path.is_dir():
        raise ValueError(f"repo.path exists but is not a directory: {repo_path}")


def _ensure_repo(repo: RepoConfig, repo_path: Path) -> bool:
    """Clone repository if needed. Returns True when clone was performed."""
    if repo_path.exists() and not repo_path.is_dir():
        raise ValueError(f"repo.path exists but is not a directory: {repo_path}")

    if not repo_path.exists():
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        _clone_repo(repo, repo_path)
        return True

    if _is_git_repo(repo_path):
        _validate_existing_remote(repo, repo_path)
        _ensure_branch(repo, repo_path)
        logger.info("Repository already exists at %s; skipping clone", repo_path)
        return False

    if any(repo_path.iterdir()):
        raise ValueError(
            f"repo.path is non-empty and not a git repository: {repo_path}. "
            "Use an empty directory or an existing git repo."
        )

    _clone_repo(repo, repo_path)
    return True


def _clone_repo(repo: RepoConfig, repo_path: Path) -> None:
    if repo.url is None:
        raise ValueError("repo.url is required when cloning a repository")

    args = ["clone"]
    if repo.branch:
        args.extend(["--branch", repo.branch, "--single-branch"])
    args.extend([repo.url, str(repo_path)])
    _git(None, *args)
    logger.info("Cloned repository %s -> %s", repo.url, repo_path)


def _ensure_branch(repo: RepoConfig, repo_path: Path) -> None:
    if not repo.branch:
        return
    current_branch = _git(repo_path, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if current_branch == repo.branch:
        return
    _git(repo_path, "checkout", repo.branch)
    logger.info("Checked out branch %s at %s", repo.branch, repo_path)


def _validate_existing_remote(repo: RepoConfig, repo_path: Path) -> None:
    if not repo.url:
        return
    try:
        existing = _git(repo_path, "remote", "get-url", "origin").strip()
    except ValueError:
        logger.warning(
            "No origin remote found at %s; skipping remote validation", repo_path
        )
        return

    if _canonical_remote(existing) != _canonical_remote(repo.url):
        raise ValueError(
            "Existing repository remote does not match repo.url. "
            f"existing={existing}, configured={repo.url}"
        )


def _canonical_remote(url: str) -> str:
    """Normalize remote URL for SSH/HTTPS equivalence checks."""
    parsed = _parse_remote(url)
    if parsed is None:
        return url.rstrip("/")
    host, repo_slug = parsed
    return f"{host.lower()}/{repo_slug.lower().removesuffix('.git')}"


def _parse_remote(url: str) -> tuple[str, str] | None:
    if is_https_url(url):
        parsed = urlparse(url)
        host = parsed.hostname
        path = parsed.path.strip("/")
        if host and path:
            return host, path
        return None

    match = re.match(r"^git@([^:]+):(.+)$", url)
    if match:
        return match.group(1), match.group(2)

    if url.startswith("ssh://"):
        parsed = urlparse(url)
        host = parsed.hostname
        path = parsed.path.strip("/")
        if host and path:
            return host, path
    return None


def _preflight_repo_auth(url: str) -> None:
    if not is_ssh_url(url):
        return

    ssh_dir = Path.home() / ".ssh"
    if not ssh_dir.is_dir():
        raise ValueError(
            f"SSH repo URL configured but SSH directory is missing: {ssh_dir}. "
            "Mount ~/.ssh into the container."
        )

    if not os.access(ssh_dir, os.R_OK):
        raise ValueError(
            f"SSH repo URL configured but SSH directory is not readable: {ssh_dir}"
        )

    strict = os.environ.get(_SSH_STRICT_ENV, "true").strip().lower()
    if strict in {"0", "false", "no"}:
        return

    host = _extract_repo_host(url)
    if host is None:
        return

    known_hosts = ssh_dir / "known_hosts"
    if not known_hosts.exists():
        raise ValueError(
            "SSH strict host key checking is enabled but ~/.ssh/known_hosts is missing."
        )

    try:
        check = subprocess.run(
            ["ssh-keygen", "-F", host, "-f", str(known_hosts)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ValueError(
            "ssh-keygen is required for SSH host verification but was not found"
        ) from exc

    if check.returncode != 0:
        raise ValueError(
            f"Host '{host}' not found in {known_hosts}. "
            f"Add it before startup (e.g. ssh-keyscan -H {host} >> ~/.ssh/known_hosts)."
        )


def _extract_repo_host(url: str) -> str | None:
    if is_https_url(url):
        parsed = urlparse(url)
        return parsed.hostname

    match = re.match(r"^git@([^:]+):.+$", url)
    if match:
        return match.group(1)

    if url.startswith("ssh://"):
        parsed = urlparse(url)
        return parsed.hostname

    return None


def _should_reindex(
    repo: RepoConfig,
    metadata: RepoInitMetadata | None,
    head_commit: str,
    context_dir: Path,
) -> bool:
    if metadata is None:
        return True

    if metadata.repo_url != (repo.url or ""):
        return True
    if metadata.repo_path != repo.path:
        return True
    if metadata.branch != repo.branch:
        return True
    if metadata.head_commit != head_commit:
        return True

    for name in CONTEXT_FILENAMES:
        if not (context_dir / name).exists():
            return True

    return False


def _generate_context(repo_path: Path, context_dir: Path) -> None:
    structure = _build_structure_doc(repo_path)
    patterns = _build_patterns_doc(repo_path)
    dependencies = _build_dependencies_doc(repo_path)

    _write_text_atomic(context_dir / "structure.md", structure)
    _write_text_atomic(context_dir / "patterns.md", patterns)
    _write_text_atomic(context_dir / "dependencies.md", dependencies)


def _build_structure_doc(repo_path: Path) -> str:
    ignored_dirs = {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
    }
    lines: list[str] = []
    max_lines = 220

    for root, dirs, files in os.walk(repo_path):
        rel_root = Path(root).relative_to(repo_path)
        depth = len(rel_root.parts)
        if depth > 3:
            dirs[:] = []
            continue

        dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
        indent = "  " * depth
        folder = rel_root.name if rel_root.parts else repo_path.name
        lines.append(f"{indent}{folder}/")

        visible_files = sorted([f for f in files if not f.startswith(".")])[:12]
        for file_name in visible_files:
            lines.append(f"{indent}  {file_name}")

        if len(lines) >= max_lines:
            break

    tree_block = "\n".join(lines[:max_lines])
    return (
        "# Architecture Overview\n\n"
        "Repository structure (depth-limited snapshot):\n\n"
        f"```text\n{tree_block}\n```\n"
    )


def _build_patterns_doc(repo_path: Path) -> str:
    ext_counts: Counter[str] = Counter()
    test_files = 0
    total_files = 0

    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.parts):
            continue
        if any(
            part in {"node_modules", ".venv", "venv", "__pycache__"}
            for part in path.parts
        ):
            continue
        total_files += 1
        ext = path.suffix.lower() or "<no_ext>"
        ext_counts[ext] += 1
        lower_name = path.name.lower()
        if lower_name.startswith("test_") or lower_name.endswith("_test.py"):
            test_files += 1

    top_exts = ext_counts.most_common(8)
    ext_lines = "\n".join(f"- `{ext}`: {count} files" for ext, count in top_exts)
    return (
        "# Coding Patterns & Conventions\n\n"
        "Static repository signals:\n\n"
        f"- Total files scanned: {total_files}\n"
        f"- Test-like files: {test_files}\n\n"
        "Most common file types:\n\n"
        f"{ext_lines}\n"
    )


def _build_dependencies_doc(repo_path: Path) -> str:
    manifests = [
        "pyproject.toml",
        "requirements.txt",
        "package.json",
        "go.mod",
    ]
    sections: list[str] = ["# Dependencies\n"]

    for name in manifests:
        path = repo_path / name
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")[:6000].strip()
        sections.append(f"## {name}\n")
        sections.append(f"```text\n{content}\n```\n")

    if len(sections) == 1:
        sections.append("No known dependency manifest files were found.\n")

    return "\n".join(sections)


def _hash_context_files(context_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name in CONTEXT_FILENAMES:
        path = context_dir / name
        if not path.exists():
            continue
        hashes[name] = _sha256(path)
    return hashes


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file_handle:
        while True:
            chunk = file_handle.read(8192)
            if not chunk:
                break
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _read_metadata(path: Path) -> RepoInitMetadata | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return RepoInitMetadata.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError):
        logger.warning("Ignoring invalid repo init metadata at %s", path)
        return None


def _write_metadata(path: Path, metadata: RepoInitMetadata) -> None:
    payload = json.dumps(metadata.model_dump(), indent=2, sort_keys=True)
    _write_text_atomic(path, payload + "\n")


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


@contextmanager
def _locked_file(path: Path, *, timeout_s: float) -> Generator[None, None, None]:
    """Acquire an exclusive lock on a file for the duration of the context."""
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as lock_file:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError(
                        f"Timed out waiting for repo init lock at {path} "
                        f"after {timeout_s:.1f}s"
                    ) from None
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _is_git_repo(path: Path) -> bool:
    git_dir = path / ".git"
    # Standard clones use a directory; worktrees and submodules use a file.
    return git_dir.exists() and (git_dir.is_dir() or git_dir.is_file())


def _git(cwd: Path | None, *args: str) -> str:
    command = ["git", *args]
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr or stdout or "unknown git error"
        raise ValueError(
            f"Git command failed ({' '.join(command)}): {details}"
        ) from exc
    return result.stdout
