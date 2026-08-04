"""Guards: examples and docs must use the current adapter construction surface.

Two retired patterns are cheap to reintroduce by copy-pasting an old example,
and nothing else would catch them (``examples/`` is excluded from the
markdown-docs CI run, and example scripts are not imported by the unit suite):

* Customer-side Parlant server ceremony — reserving ports, holding
  ``p.Server`` open, building the Band tool list by hand. The adapter owns all
  of that now.
* Bridge adapters taking Band credentials (``api_key=`` / ``rest_url=``) that
  the caller already gives ``Agent.create()``. The runtime injects the
  platform connection instead.
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


def example_scripts() -> list[Path]:
    return sorted(EXAMPLES_ROOT.rglob("*.py"))


def customer_docs() -> list[Path]:
    """Markdown a customer reads: example READMEs + top-level repo docs."""
    docs = sorted(EXAMPLES_ROOT.rglob("*.md"))
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
