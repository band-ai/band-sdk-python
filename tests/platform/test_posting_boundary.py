"""Chat-send boundary test.

``band.platform.posting.post_message``/``post_event`` is the single choke
point that enforces the platform's content rules (visible-content, the
events content cap) for every agent-side send. A caller that reaches
``create_agent_chat_message``/``create_agent_chat_event`` directly bypasses
those rules -- this scans real source files for such a call outside the
allowlist below, so a new bypass fails a test instead of shipping unguarded.

This scans for the method calls via AST, not imports -- no import-time side
effects, no needing every extra installed.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.paths import REPO_ROOT

_GUARDED_METHOD_NAMES = frozenset(
    {"create_agent_chat_message", "create_agent_chat_event"}
)

# The only places a guarded method may be called directly.
#
# posting.py is the choke point itself. cli/trigger.py is a human-operated
# CLI script, not an agent tool-call or platform relay -- a blank --message
# should fail loudly for the operator, not be silently refused the way an
# LLM's tool call is, and its human-auth branch (human_api_messages) posts
# through a different REST namespace `post_message`/`post_event` don't cover
# anyway.
_ALLOWED_BYPASS_FILES: frozenset[Path] = frozenset(
    REPO_ROOT / path
    for path in (
        "src/band/platform/posting.py",
        "src/band/cli/trigger.py",
    )
)

_SCAN_ROOT = REPO_ROOT / "src" / "band"


def _guarded_method_calls(source: str) -> set[str]:
    """The guarded method names *source* calls directly, as ``x.<name>(...)``."""
    tree = ast.parse(source)
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _GUARDED_METHOD_NAMES
    }


def _bypass_offenders() -> list[tuple[Path, set[str]]]:
    """Files under the scan root, outside the allowlist, that call a guarded
    method directly instead of going through post_message/post_event."""
    offenders = []
    for path in _SCAN_ROOT.rglob("*.py"):
        if path in _ALLOWED_BYPASS_FILES:
            continue
        calls = _guarded_method_calls(path.read_text(encoding="utf-8"))
        if calls:
            offenders.append((path.relative_to(REPO_ROOT), calls))
    return sorted(offenders)


def test_chat_sends_go_through_the_posting_choke_point() -> None:
    offenders = _bypass_offenders()

    assert not offenders, (
        f"Found direct calls to guarded REST methods outside the allowlist: {offenders}. "
        "Route the call through band.platform.posting.post_message/post_event instead "
        "(or, if this really is a new deliberate exception, add it to "
        "_ALLOWED_BYPASS_FILES with why)."
    )


def test_allowlist_entries_still_exist() -> None:
    """Catch a stale allowlist entry (a file the plan says should be deleted
    by a given step, but the deletion never landed -- or a typo'd path)."""
    missing = [
        path.relative_to(REPO_ROOT)
        for path in _ALLOWED_BYPASS_FILES
        if not path.is_file()
    ]
    assert not missing, f"Allowlisted paths no longer exist: {missing}"
