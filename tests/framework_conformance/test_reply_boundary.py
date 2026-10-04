"""Reply boundary test.

A ``send_message`` call counts as the turn's reply, so only the model's own
words may go through it: the tool methods, ``deliver_reply``/``relay_reply``,
and the framework tool implementations that call them. An adapter's own post
(an approval prompt, a busy notice, a status reply) goes through
``send_notice``, or it would stand in for the model's answer and suppress the
final-text relay. This scans ``src/band`` via AST for a direct
``x.send_message(...)`` call outside the allowlist, so a new adapter post on
``send_message`` fails here instead of silently counting as a reply.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.paths import REPO_ROOT

_SCAN_ROOT = REPO_ROOT / "src" / "band"

# The model's tool implementations and the relay that carries its words, plus
# two files where ``send_message`` is an unrelated client's method.
_ALLOWED_DIRS: frozenset[Path] = frozenset({_SCAN_ROOT / "runtime" / "tools"})
_ALLOWED_FILES: frozenset[Path] = frozenset(
    _SCAN_ROOT / path
    for path in (
        "core/delivery.py",
        "integrations/crewai/catalog.py",
        "integrations/parlant/tools.py",
        # Forwards to the inner tools' send_message, which records the reply.
        "integrations/claude_sdk/dedup_tools.py",
        # Exempt from the turn verdict: the flow posts its own outcomes.
        "adapters/crewai_flow.py",
        # The A2A client's own method.
        "integrations/a2a/adapter.py",
        # The Phoenix protocol's own method.
        "testing/phoenix_server.py",
    )
)


def _calls_send_message(source: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "send_message"
        for node in ast.walk(ast.parse(source))
    )


def _is_allowed(path: Path) -> bool:
    return path in _ALLOWED_FILES or any(d in path.parents for d in _ALLOWED_DIRS)


def test_adapter_posts_never_count_as_the_model_reply() -> None:
    offenders = sorted(
        path.relative_to(REPO_ROOT)
        for path in _SCAN_ROOT.rglob("*.py")
        if not _is_allowed(path) and _calls_send_message(path.read_text("utf-8"))
    )

    assert not offenders, (
        f"Direct send_message calls outside the model's reply paths: {offenders}. "
        "Post an adapter's own message with tools.send_notice (and settle the "
        "turn with tools.turn.settle() when it ends the turn), or relay the "
        "model's words through band.core.delivery.deliver_reply/relay_reply."
    )


def test_allowlist_entries_still_exist() -> None:
    missing = [
        path.relative_to(REPO_ROOT)
        for path in _ALLOWED_FILES | _ALLOWED_DIRS
        if not path.exists()
    ]
    assert not missing, f"Allowlisted paths no longer exist: {missing}"
