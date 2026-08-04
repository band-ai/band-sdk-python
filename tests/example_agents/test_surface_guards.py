"""Guards: examples and docs must use the current adapter construction surface.

Three retired patterns are cheap to reintroduce by copy-pasting an old example,
and nothing else would catch them (``examples/`` is excluded from the
markdown-docs CI run, and example scripts are not imported by the unit suite):

* Customer-side Parlant server ceremony — reserving ports, holding
  ``p.Server`` open, building the Band tool list by hand. The adapter owns all
  of that now.
* Bridge adapters taking Band credentials (``api_key=`` / ``rest_url=``) that
  the caller already gives ``Agent.create()``. The runtime injects the
  platform connection instead.
* The ``features=AdapterFeatures(...)`` wrapper and its legacy boolean shims
  (``enable_execution_reporting``, ``enable_memory_tools``, and the Codex/
  Letta/Opencode config-boolean variants). Adapters take ``emit=``/
  ``capabilities=``/... directly now (see ``FeatureKwargs``); ``Emit.EXECUTION``
  was renamed ``Emit.TOOL_CALLS`` in the same change.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.paths import EXAMPLES_ROOT, REPO_ROOT

# Adapter constructors that must not take Band credentials in examples/docs.
# ParlantAdapter is included for its retired additional_tools parameter.
FORBIDDEN_CONSTRUCTOR_KWARGS: dict[str, frozenset[str]] = {
    "SlackAdapter": frozenset({"api_key", "rest_url"}),
    "A2AGatewayAdapter": frozenset({"api_key", "rest_url"}),
    "BandACPServerAdapter": frozenset({"api_key", "rest_url"}),
    "ACPClientAdapter": frozenset({"rest_url"}),
    "CopilotACPAdapterConfig": frozenset({"rest_url"}),
    "ParlantAdapter": frozenset({"additional_tools"}),
}

# Parlant server plumbing that customer-facing code must never touch — the
# adapter owns ports/server/tool wiring. (SDK internals and the E2E toolkit
# legitimately use these; examples and docs must not.)
PARLANT_CEREMONY_TOKENS = (
    "reserve_server_ports",
    "create_parlant_tools",
)

# Platform-URL env boilerplate is retired: Agent.create resolves BAND_WS_URL /
# BAND_REST_URL itself (band.config.PlatformSettings). Examples never read
# these two env vars by hand.
URL_BOILERPLATE_TOKENS = (
    'os.getenv("BAND_WS_URL")',
    'os.getenv("BAND_REST_URL")',
    'os.environ.get("BAND_WS_URL")',
    'os.environ.get("BAND_REST_URL")',
    'os.environ["BAND_WS_URL"]',
    'os.environ["BAND_REST_URL"]',
    "BAND_WS_URL environment variable is required",
    "BAND_REST_URL environment variable is required",
)

# The wrapper-object feature surface and its legacy boolean shims are retired:
# adapters take emit=/capabilities=/... directly (FeatureKwargs), and the
# Emit vocabulary member was renamed EXECUTION -> TOOL_CALLS in the same change.
RETIRED_FEATURE_SURFACE_TOKENS = (
    "features=AdapterFeatures(",
    "Emit.EXECUTION",
    "enable_execution_reporting",
    "enable_memory_tools",
    "emit_thought_events",
    "enable_task_events",
)


def example_scripts() -> list[Path]:
    """Customer-facing adapter-construction scripts: examples/ + docker/*/runner.py.

    The docker runners are their own deployment entrypoints, not under
    examples/, but construct adapters the exact same way a customer would.
    """
    scripts = sorted(EXAMPLES_ROOT.rglob("*.py"))
    scripts += sorted((REPO_ROOT / "docker").glob("*/runner.py"))
    return scripts


def customer_docs() -> list[Path]:
    """Markdown a customer reads: example READMEs + top-level + per-adapter docs."""
    docs = sorted(EXAMPLES_ROOT.rglob("*.md"))
    docs += sorted((REPO_ROOT / "docs" / "adapters").glob("*.md"))
    docs += [REPO_ROOT / "README.md", REPO_ROOT / "CLAUDE.md"]
    return docs


def _call_name(node: ast.Call) -> str | None:
    match node.func:
        case ast.Name(id=name):
            return name
        case ast.Attribute(attr=name):
            return name
    return None


@pytest.mark.parametrize(
    "path", example_scripts(), ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_example_uses_current_construction_surface(path: Path) -> None:
    source = path.read_text(encoding="utf-8")

    for token in PARLANT_CEREMONY_TOKENS:
        assert token not in source, (
            f"{path}: uses retired Parlant ceremony '{token}' — the adapter "
            "owns ports/server/tools now (see examples/parlant/)"
        )

    for token in URL_BOILERPLATE_TOKENS:
        assert token not in source, (
            f"{path}: hand-rolls platform-URL env handling ('{token}') — "
            "Agent.create resolves BAND_WS_URL/BAND_REST_URL itself; omit the "
            "ws_url/rest_url arguments (or use band.config.PlatformSettings)"
        )

    for token in RETIRED_FEATURE_SURFACE_TOKENS:
        assert token not in source, (
            f"{path}: uses retired feature surface '{token}' — pass "
            "emit=/capabilities=/... directly to the adapter constructor "
            "instead of features=AdapterFeatures(...); Emit.EXECUTION is now "
            "Emit.TOOL_CALLS"
        )

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        forbidden = FORBIDDEN_CONSTRUCTOR_KWARGS.get(name or "")
        if not forbidden:
            continue
        used = {kw.arg for kw in node.keywords if kw.arg} & forbidden
        assert not used, (
            f"{path}:{node.lineno}: {name}({', '.join(sorted(used))}=...) — "
            "retired parameter(s); Band credentials flow once via Agent, and "
            "the runtime injects the platform connection into the adapter"
        )


@pytest.mark.parametrize(
    "path", customer_docs(), ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_doc_uses_current_construction_surface(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    for token in PARLANT_CEREMONY_TOKENS:
        assert token not in text, (
            f"{path}: documents retired Parlant ceremony '{token}' — the "
            "adapter owns ports/server/tools now"
        )

    for token in RETIRED_FEATURE_SURFACE_TOKENS:
        assert token not in text, (
            f"{path}: documents retired feature surface '{token}' — pass "
            "emit=/capabilities=/... directly to the adapter constructor "
            "instead of features=AdapterFeatures(...); Emit.EXECUTION is now "
            "Emit.TOOL_CALLS"
        )
