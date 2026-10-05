"""Reply boundary test.

A ``send_message``, ``deliver_reply`` or ``relay_reply`` call counts as the
turn's reply, so only the model's own words may go through one. An adapter's
own post (an approval prompt, a busy notice, a status reply) goes through
``send_notice``, or it would stand in for the model's answer and suppress the
final-text relay. This scans ``src/band`` via AST and pins every such call
outside the tool implementations, per file with the reason it carries the
model's words, so a new reply call anywhere fails here until it is justified.
"""

from __future__ import annotations

import ast
from collections import Counter

from tests.paths import SRC_ROOT

#: The model's tool implementations, where every reply call is the model's.
TOOLS_DIR = SRC_ROOT / "runtime" / "tools"

REPLY_CALLS = frozenset({"send_message", "deliver_reply", "relay_reply"})

#: Reply calls per file outside ``TOOLS_DIR``, and why each carries the model's words.
ALLOWED_REPLY_CALLS: dict[str, tuple[Counter[str], str]] = {
    "core/delivery.py": (
        Counter(send_message=1, deliver_reply=1),
        "deliver_reply posts the reply; relay_reply delegates to it",
    ),
    "adapters/codex.py": (Counter(relay_reply=1), "the model's final text"),
    "adapters/copilot_sdk.py": (
        Counter(relay_reply=1, deliver_reply=1),
        "the model's final text, and its ask_user question as the turn's reply",
    ),
    "adapters/letta.py": (Counter(relay_reply=1), "the model's final text"),
    "adapters/opencode/adapter.py": (Counter(relay_reply=1), "the model's final text"),
    "adapters/parlant.py": (Counter(relay_reply=1), "the engine's message"),
    "integrations/acp/room_emitter.py": (
        Counter(relay_reply=1),
        "the agent's held text runs",
    ),
    "integrations/crewai/catalog.py": (Counter(send_message=1), "the crew's tool"),
    "integrations/parlant/tools.py": (Counter(send_message=1), "the engine's tool"),
    "integrations/claude_sdk/dedup_tools.py": (
        Counter(send_message=1),
        "forwards the model's tool call to the inner tools",
    ),
    "integrations/a2a/adapter.py": (
        Counter(send_message=1, deliver_reply=3),
        "the remote agent's answer; send_message is the A2A client's method",
    ),
    "adapters/crewai_flow.py": (
        Counter(send_message=3),
        "exempt from the verdict: the flow posts its own outcomes",
    ),
    "testing/phoenix_server.py": (
        Counter(send_message=2),
        "the Phoenix protocol's own method",
    ),
}


def reply_calls(source: str) -> Counter[str]:
    """How many times ``source`` calls each reply path."""
    names = (
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute | ast.Name)
    )
    return Counter(name for name in names if name in REPLY_CALLS)


def test_every_reply_call_carries_the_model_words() -> None:
    found = {
        path.relative_to(SRC_ROOT).as_posix(): calls
        for path in SRC_ROOT.rglob("*.py")
        if TOOLS_DIR not in path.parents
        and (calls := reply_calls(path.read_text("utf-8")))
    }

    assert found == {path: calls for path, (calls, _) in ALLOWED_REPLY_CALLS.items()}, (
        "A reply call changed outside the model's reply paths. Post an adapter's "
        "own message with tools.send_notice (and tools.turn.settle() when it ends "
        "the turn); relay the model's words through relay_reply and record why "
        "in ALLOWED_REPLY_CALLS."
    )
