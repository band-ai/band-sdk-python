"""Test dependency gates and availability checks (pytest-free).

A ``Dep`` is a capability a test (or an adapter builder) needs: a model-provider
key, an external CLI/server, or a dependency lane. This module owns the *facts* --
one ``DepSpec`` per ``Dep`` (the pure ``settings``/environment predicate that
decides whether it is available, the human reason when it is not, and the **CI
lane** the dep belongs to). It deliberately imports no pytest, so the toolkit
(registry, builders) can reference ``Dep`` without pulling in the test framework.
The pytest glue (the ``@requires`` marker and the ``pytest.fail`` on an absent
dep) lives in ``..requires``.

Lanes: CI can't run the whole fail-loud matrix in one job -- crewai conflicts with
the default venv's deps, and the external-backend adapters (codex/opencode/letta)
need backends stood up that the plain ``dev`` job doesn't provide. So a dep names a
**lane** (a CI job): crewai gets its own venv lane, and the backend deps share one
``backends`` lane (they all install the ``dev`` extra and their setups co-run in
that job). ``LANE_EXTRAS`` maps a lane to the ``uv`` extra it installs; provider-key
deps stay in the shared ``dev`` lane. The lane partition is derived from these facts
(see ``toolkit.ci_lanes.ci_lanes``), never a hand-maintained list.

Validation policy: a missing requirement **fails** a test, it never skips. Skipping
on absent config hides misconfiguration as false-green. The only thing that skips
is the ``E2E_TESTS_ENABLED`` master switch. That policy is enforced by ``require_dep``
in ``..requires``; this module just reports availability.
"""

from __future__ import annotations

import importlib.util
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path
from urllib.parse import urlparse

from tests.e2e.baseline.settings import BaselineSettings
from tests.paths import REPO_ROOT

# REPO_ROOT: used to reject a Codex working directory inside the SDK checkout,
# and by the rest of the toolkit (e.g. locating the e2e workflow).

_LETTA_CLOUD_HOST = "api.letta.com"


def _is_letta_cloud(base_url: str) -> bool:
    """Whether ``base_url`` points at Letta Cloud, matching the host only.

    Ignores scheme, case, port, and path so ``https://api.letta.com/v1``,
    ``http://api.letta.com``, ``API.LETTA.COM``, and a trailing slash all count as
    cloud (which needs an API key) rather than misclassifying as a self-hosted
    server (which would be reported available with no key).
    """
    url = base_url.strip()
    if not url:
        return False
    # Give urlparse a netloc to populate when the value is a bare host (no scheme).
    if "//" not in url:
        url = "//" + url
    return (urlparse(url).hostname or "").lower() == _LETTA_CLOUD_HOST


class Lane(StrEnum):
    """Typed handle for a CI lane (one e2e job).

    Each member's *value* is the lane id used in ``BAND_E2E_LANE`` and the workflow
    matrix. Like ``Adapter``, it exists so lanes are referenced by a typed handle,
    never a magic string. Lane ids are content-based (what the lane runs); they are
    decoupled from the ``uv`` extra a lane installs (see ``LANE_EXTRAS``).
    """

    CORE = "core"
    CREWAI = "crewai"
    BACKENDS = "backends"
    GOOGLE = "google"
    LETTA = "letta"


# The shared default lane: every provider-key adapter with no special isolation
# need runs here (anthropic/openai-family frameworks).
DEFAULT_LANE = Lane.CORE


class Extra(StrEnum):
    """A ``uv`` extra a lane installs (``uv sync --extra <value>``).

    Each member's *value* is an extra name declared in ``pyproject.toml``
    ``[project.optional-dependencies]`` -- the contract is that contract. Typed so
    ``LANE_EXTRAS`` references extras by handle rather than a bare magic string.
    """

    DEV = "dev"
    DEV_CREWAI = "dev-crewai"


# Lane -> the ``uv`` extra a lane's job installs. Lane id and extra are separate:
# several lanes share the ``dev`` extra but are split out for isolation (their own
# server/CLI, or rate-limit flakiness). crewai is the one lane that *needs* its own
# conflicting extra (see pyproject [tool.uv] conflicts).
LANE_EXTRAS: dict[Lane, Extra] = {
    Lane.CORE: Extra.DEV,
    Lane.CREWAI: Extra.DEV_CREWAI,
    Lane.BACKENDS: Extra.DEV,
    # The Google adapters (gemini/google_adk) share the ``dev`` extra but run in
    # their own lane so their free-tier rate-limit flakiness is isolated from the
    # rest of the provider-key adapters (and can be run/keyed separately).
    Lane.GOOGLE: Extra.DEV,
    # Letta runs the ``dev`` extra but stands up its own self-hosted server, so it
    # gets its own lane (split out of ``backends``, which keeps codex + opencode).
    Lane.LETTA: Extra.DEV,
}


# Lanes whose CI job is Linux-only — their server backend has no Windows story.
# OS is otherwise a workflow concern (the runner image); this is the one fact
# about it the registry owns, because it is a property of what a lane *runs*.
#
# Letta stands up a self-hosted server, and neither way to run it works on the
# windows-latest runner: the ``letta/letta`` image is Linux-only (the Windows
# runner only runs Windows containers), and Letta's own install guide supports a
# native install on macOS / Linux / WSL only, not raw Windows
# (https://docs.letta.com/guides/server/source). Every other lane runs on every
# OS the e2e matrix offers.
LINUX_ONLY_LANES: frozenset[Lane] = frozenset({Lane.LETTA})


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
    LETTA = "letta"  # a self-hosted LETTA_BASE_URL (or a Letta Cloud key)
    CREWAI = "crewai"  # the crewai package is importable (the dev-crewai lane)
    COPILOT_CLI = "copilot_cli"  # the `copilot` CLI reachable on PATH (ACP backend)


@dataclass(frozen=True)
class DepSpec:
    """Everything known about one ``Dep``, in a single record.

    One record per ``Dep`` is the single source of truth. ``available`` is a pure
    function of settings/environment (no pytest, no side effects); ``reason`` is
    shown when it returns False; ``lane`` is the CI lane the dep gates (default: the
    shared ``dev`` lane -- only backend/venv deps name their own lane).
    """

    available: Callable[[BaselineSettings], bool]
    reason: str
    lane: Lane = DEFAULT_LANE


def _google_available(settings: BaselineSettings) -> bool:
    """Gemini Developer API key present, or Vertex AI fully configured."""
    creds = settings.llm_credentials
    if creds.google_api_key or creds.gemini_api_key:
        return True
    return creds.google_genai_use_vertexai == "true" and bool(
        creds.google_cloud_project
    )


def _cli_on_path(command: str, default_binary: str) -> bool:
    """Whether the CLI binary (from an override ``command`` or ``default_binary``) is on PATH."""
    binary = command.split()[0] if command.strip() else default_binary
    return shutil.which(binary) is not None


def _codex_cli_available(settings: BaselineSettings) -> bool:
    """The Codex CLI (or the binary named by ``CODEX_COMMAND``) is on PATH."""
    return _cli_on_path(settings.backends.codex_command, "codex")


def _copilot_cli_available(settings: BaselineSettings) -> bool:
    """The Copilot CLI (or the binary named by ``COPILOT_COMMAND``) is on PATH."""
    return _cli_on_path(settings.backends.copilot_command, "copilot")


def _codex_cwd_available(settings: BaselineSettings) -> bool:
    """``CODEX_CWD`` is an existing, explicitly-disposable dir outside the repo.

    Codex can write to its working directory, so the E2E run must point it at a
    throwaway path and opt in via ``E2E_CODEX_CWD_IS_DISPOSABLE`` -- never the SDK
    checkout.
    """
    cwd = settings.backends.codex_cwd
    if not cwd or not settings.backends.codex_cwd_is_disposable:
        return False
    path = Path(cwd).expanduser().resolve()
    if not path.is_dir():
        return False
    return path != REPO_ROOT and REPO_ROOT not in path.parents


def _letta_llm_provider_available(settings: BaselineSettings) -> bool:
    """Keys the self-hosted Letta server needs for its configured model/embedding."""
    llm = settings.llm_credentials
    backends = settings.backends
    specs = (backends.letta_model, backends.letta_embedding)
    for spec in specs:
        provider = spec.split("/", 1)[0].lower() if spec else ""
        match provider:
            case "openai":
                if not llm.openai_api_key:
                    return False
            case "anthropic":
                if not llm.anthropic_api_key:
                    return False
            case "google" | "gemini":
                if not _google_available(settings):
                    return False
    return True


def _letta_available(settings: BaselineSettings) -> bool:
    """A reachable Letta server plus the key its own LLM calls need.

    A self-hosted (non-cloud) ``LETTA_BASE_URL`` needs no Letta key, but the
    Letta server makes its own model calls using the provider named in
    ``LETTA_MODEL`` / ``LETTA_EMBEDDING``. Letta Cloud needs its API key.
    Env-only predicate — no network calls; reachability failures surface
    fail-loud at run time.
    """
    backends = settings.backends
    base_url = backends.letta_base_url.strip()
    if base_url and not _is_letta_cloud(base_url):
        return _letta_llm_provider_available(settings)
    # Unset/blank base_url defaults to (or means) Letta Cloud, which needs a key.
    return bool(backends.letta_api_key)


# The one table: Dep -> its facts. Every Dep MUST appear (enforced by
# ``validate_dep_tables``), so a newly-added Dep cannot silently escape the lane
# partition or the gate. A dep that gates its own lane sets ``lane=`` to a key in
# ``LANE_EXTRAS``.
_DEPS: dict[Dep, DepSpec] = {
    Dep.OPENAI: DepSpec(
        lambda s: bool(s.llm_credentials.openai_api_key),
        "OPENAI_API_KEY not set",
    ),
    Dep.ANTHROPIC: DepSpec(
        lambda s: bool(s.llm_credentials.anthropic_api_key),
        "ANTHROPIC_API_KEY not set",
    ),
    Dep.GOOGLE: DepSpec(
        _google_available,
        "GOOGLE_API_KEY/GEMINI_API_KEY or Vertex AI env "
        "(GOOGLE_GENAI_USE_VERTEXAI + GOOGLE_CLOUD_PROJECT) not set",
        lane=Lane.GOOGLE,
    ),
    Dep.CODEX_CLI: DepSpec(
        _codex_cli_available, "Codex CLI not found on PATH", lane=Lane.BACKENDS
    ),
    # The Copilot CLI (unlike the self-downloading Copilot SDK) must be stood up on
    # PATH, so its ACP backend rides the ``backends`` lane alongside codex/opencode.
    Dep.COPILOT_CLI: DepSpec(
        _copilot_cli_available, "Copilot CLI not found on PATH", lane=Lane.BACKENDS
    ),
    Dep.CODEX_CWD: DepSpec(
        _codex_cwd_available,
        "CODEX_CWD must be an existing disposable dir outside the repo "
        "with E2E_CODEX_CWD_IS_DISPOSABLE=true",
        lane=Lane.BACKENDS,
    ),
    Dep.OPENCODE_SERVER: DepSpec(
        lambda s: bool(s.backends.opencode_base_url),
        "OPENCODE_BASE_URL not set (a running OpenCode server is required)",
        lane=Lane.BACKENDS,
    ),
    Dep.LETTA: DepSpec(
        _letta_available,
        "a self-hosted LETTA_BASE_URL + provider key for LETTA_MODEL/LETTA_EMBEDDING "
        "(e.g. OPENAI_API_KEY or ANTHROPIC_API_KEY), or a Letta Cloud LETTA_API_KEY, "
        "not set",
        lane=Lane.LETTA,
    ),
    Dep.CREWAI: DepSpec(
        lambda _s: importlib.util.find_spec("crewai") is not None,
        "crewai is not importable (install the dev-crewai lane)",
        lane=Lane.CREWAI,
    ),
}


def dep_lane(dep: Dep) -> Lane:
    """The CI lane ``dep`` gates: its own lane, else the shared default lane."""
    return _DEPS[dep].lane


def lane_extra(lane: Lane) -> Extra:
    """The ``uv`` extra a lane installs (raises ``KeyError`` for an unknown lane)."""
    return LANE_EXTRAS[lane]


def requirement_reason(dep: Dep, settings: BaselineSettings) -> str | None:
    """Return why ``dep`` is unavailable, or ``None`` when it is satisfied."""
    spec = _DEPS[dep]
    return None if spec.available(settings) else spec.reason


def validate_dep_tables() -> None:
    """Fail loudly on a ``Dep`` that is unspecified or names an unknown lane.

    Every ``Dep`` must have a ``DepSpec``, and every dep's ``lane`` must be a known
    lane (in ``LANE_EXTRAS``). A new member that skips either surfaces here
    (mirroring the adapter discovery guard) rather than silently falling through
    the lane partition.
    """
    missing = [dep for dep in Dep if dep not in _DEPS]
    unknown_lane = [dep for dep, spec in _DEPS.items() if spec.lane not in LANE_EXTRAS]
    if missing or unknown_lane:
        raise AssertionError(
            "Dep table is invalid:\n"
            f"  Dep members without a DepSpec: {missing}\n"
            f"  Dep with a lane not in LANE_EXTRAS: {unknown_lane}"
        )
