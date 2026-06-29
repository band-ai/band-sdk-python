"""Test-dependency requirements and their availability checks (pytest-free).

A ``Dep`` is a capability a test (or an adapter builder) needs: a model-provider
key, an external CLI/server, or a dependency lane. This module owns the *facts* --
one ``DepSpec`` per ``Dep`` (its CI kind, the pure ``settings``/environment
predicate that decides whether it is available, the human reason when it is not,
and the ``uv`` extra a venv-gated dep needs). It deliberately imports no pytest, so
the toolkit (registry, builders) can reference ``Dep`` without pulling in the test
framework. The pytest glue (the ``@requires`` marker and the ``pytest.fail`` on an
absent requirement) lives in ``..requires``.

Validation policy: a missing requirement **fails** a test, it never skips. Skipping
on absent config hides misconfiguration as false-green. The only thing that skips
is the ``E2E_TESTS_ENABLED`` master switch. That policy is enforced by ``require_dep``
in ``..requires``; this module just reports availability.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

from tests.e2e.baseline.settings import BaselineSettings

# Repo root, used to reject a Codex working directory that lives inside the SDK
# checkout (a destructive-agent guard). settings.py is at tests/e2e/baseline/.
_REPO_ROOT = Path(__file__).resolve().parents[4]

_LETTA_CLOUD_HOST = "https://api.letta.com"


class Dep(Enum):
    """A capability a test or adapter builder can require.

    Provider keys gate the LLM adapters; the remaining members gate adapters with
    an external prerequisite (a CLI binary, a running server, a dependency lane).
    Every member is described by a ``DepSpec`` in ``_DEPS`` below.
    """

    # Model-provider keys.
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"  # Gemini Developer API key or a configured Vertex AI env.

    # External / infra prerequisites for the non-key adapters.
    CODEX_CLI = "codex_cli"  # the `codex` CLI reachable on PATH
    CODEX_CWD = "codex_cwd"  # an explicit, disposable working dir outside the repo
    OPENCODE_SERVER = "opencode_server"  # OPENCODE_BASE_URL of a running server
    LETTA_CLOUD = "letta_cloud"  # Letta Cloud key (or a self-hosted base_url)
    CREWAI = "crewai"  # the crewai package is importable (the dev-crewai lane)


class DepKind(Enum):
    """How CI can satisfy a ``Dep`` -- the axis the lane partition is derived from.

    A ``PROVIDER_KEY`` is satisfiable from a GitHub secret, so its adapters run in
    CI directly. ``INFRA`` needs an external CLI/server/cloud not present in CI, so
    its adapters have no lane yet (they skip with a reason until a backend is
    wired). ``VENV`` is a dependency-lane gate: its adapters run only in the lane of
    the ``uv`` extra named by ``DepSpec.extra``.
    """

    PROVIDER_KEY = "provider_key"
    INFRA = "infra"
    VENV = "venv"


# The default CI lane's ``uv`` extra. Every adapter with no VENV requirement (i.e.
# every provider-key / infra adapter) installs and -- when runnable -- runs here.
DEFAULT_EXTRA = "dev"


@dataclass(frozen=True)
class DepSpec:
    """Everything known about one ``Dep``, in a single record.

    One record per ``Dep`` is the single source of truth: there are no parallel
    tables to keep in sync. ``available`` is a pure function of
    settings/environment (no pytest, no side effects); ``reason`` is shown when it
    returns False; ``extra`` is the ``uv`` lane and is only meaningful for a VENV
    dep (it defaults to the shared lane for provider-key / infra deps).
    """

    kind: DepKind
    available: Callable[[BaselineSettings], bool]
    reason: str
    extra: str = DEFAULT_EXTRA


def _google_available(settings: BaselineSettings) -> bool:
    """Gemini Developer API key present, or Vertex AI fully configured."""
    if settings.llm_credentials.google_api_key or os.environ.get("GEMINI_API_KEY"):
        return True
    return os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") == "true" and bool(
        os.environ.get("GOOGLE_CLOUD_PROJECT")
    )


def _codex_cli_available(_settings: BaselineSettings) -> bool:
    """The Codex CLI (or the binary named by ``CODEX_COMMAND``) is on PATH."""
    command = os.environ.get("CODEX_COMMAND", "")
    binary = command.split()[0] if command.strip() else "codex"
    return shutil.which(binary) is not None


def _codex_cwd_available(_settings: BaselineSettings) -> bool:
    """``CODEX_CWD`` is an existing, explicitly-disposable dir outside the repo.

    Codex can write to its working directory, so the E2E run must point it at a
    throwaway path and opt in via ``E2E_CODEX_CWD_IS_DISPOSABLE`` -- never the SDK
    checkout.
    """
    cwd = os.environ.get("CODEX_CWD")
    if not cwd or os.environ.get("E2E_CODEX_CWD_IS_DISPOSABLE") != "true":
        return False
    path = Path(cwd).expanduser().resolve()
    if not path.is_dir():
        return False
    return path != _REPO_ROOT and _REPO_ROOT not in path.parents


def _letta_available(_settings: BaselineSettings) -> bool:
    """Letta Cloud key present, or a self-hosted (non-cloud) ``LETTA_BASE_URL``."""
    base_url = os.environ.get("LETTA_BASE_URL", _LETTA_CLOUD_HOST).rstrip("/")
    if base_url != _LETTA_CLOUD_HOST:
        return True  # self-hosted server needs no cloud key
    if not os.environ.get("LETTA_API_KEY") or not os.environ.get("MCP_SERVER_URL"):
        return False
    # Letta Cloud reaches the MCP server itself, so it must be publicly routable.
    host = urlparse(os.environ["MCP_SERVER_URL"]).hostname
    return host not in {"localhost", "127.0.0.1", "0.0.0.0"}


# The one table: Dep -> its facts. Every Dep MUST appear (enforced by
# ``validate_dep_tables``), so a newly-added Dep cannot silently escape the lane
# partition or the gate. A VENV dep MUST set ``extra`` (its uv lane); the extra
# name is the contract with ``pyproject.toml`` ``[project.optional-dependencies]``.
_DEPS: dict[Dep, DepSpec] = {
    Dep.OPENAI: DepSpec(
        DepKind.PROVIDER_KEY,
        lambda s: bool(s.llm_credentials.openai_api_key),
        "OPENAI_API_KEY not set",
    ),
    Dep.ANTHROPIC: DepSpec(
        DepKind.PROVIDER_KEY,
        lambda s: bool(s.llm_credentials.anthropic_api_key),
        "ANTHROPIC_API_KEY not set",
    ),
    Dep.GOOGLE: DepSpec(
        DepKind.PROVIDER_KEY,
        _google_available,
        "GOOGLE_API_KEY/GEMINI_API_KEY or Vertex AI env "
        "(GOOGLE_GENAI_USE_VERTEXAI + GOOGLE_CLOUD_PROJECT) not set",
    ),
    Dep.CODEX_CLI: DepSpec(
        DepKind.INFRA, _codex_cli_available, "Codex CLI not found on PATH"
    ),
    Dep.CODEX_CWD: DepSpec(
        DepKind.INFRA,
        _codex_cwd_available,
        "CODEX_CWD must be an existing disposable dir outside the repo "
        "with E2E_CODEX_CWD_IS_DISPOSABLE=true",
    ),
    Dep.OPENCODE_SERVER: DepSpec(
        DepKind.INFRA,
        lambda _s: bool(os.environ.get("OPENCODE_BASE_URL")),
        "OPENCODE_BASE_URL not set (a running OpenCode server is required)",
    ),
    Dep.LETTA_CLOUD: DepSpec(
        DepKind.INFRA,
        _letta_available,
        "LETTA_API_KEY + MCP_SERVER_URL (cloud) or a self-hosted LETTA_BASE_URL "
        "not set",
    ),
    Dep.CREWAI: DepSpec(
        DepKind.VENV,
        lambda _s: importlib.util.find_spec("crewai") is not None,
        "crewai is not importable (install the dev-crewai lane)",
        extra="dev-crewai",
    ),
}


def dep_kind(dep: Dep) -> DepKind:
    """The ``DepKind`` of ``dep`` (raises ``KeyError`` if unspecified)."""
    return _DEPS[dep].kind


def dep_extra(dep: Dep) -> str:
    """The ``uv`` extra ``dep`` needs: its VENV extra, else the default lane's."""
    return _DEPS[dep].extra


def requirement_reason(dep: Dep, settings: BaselineSettings) -> str | None:
    """Return why ``dep`` is unavailable, or ``None`` when it is satisfied."""
    spec = _DEPS[dep]
    return None if spec.available(settings) else spec.reason


def validate_dep_tables() -> None:
    """Fail loudly on a ``Dep`` that is unspecified or mis-specified.

    Every ``Dep`` must have a ``DepSpec``, and a VENV dep must name a non-default
    ``extra`` (its own uv lane). A new member that skips either surfaces here
    (mirroring the adapter discovery guard) rather than silently falling through
    the lane partition.
    """
    missing = [dep for dep in Dep if dep not in _DEPS]
    venv_in_default = [
        dep
        for dep, spec in _DEPS.items()
        if spec.kind is DepKind.VENV and spec.extra == DEFAULT_EXTRA
    ]
    if missing or venv_in_default:
        raise AssertionError(
            "Dep table is invalid:\n"
            f"  Dep members without a DepSpec: {missing}\n"
            f"  VENV dep left in the default extra (set extra=): {venv_in_default}"
        )
