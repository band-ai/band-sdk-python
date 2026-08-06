"""Shared builders for Strands Converse-format test fixtures.

Used by both the converter tests (tests/converters/test_strands.py) and the
adapter tests (tests/adapters/test_strands_adapter.py) so the two suites pin
the same message shape rather than each hand-rolling their own history dicts.
Pure dict-building — no dependency on the strands package itself.
"""

from __future__ import annotations

import json


def tool_call(name: str, args: dict, call_id: str) -> dict:
    return {
        "role": "assistant",
        "content": json.dumps({"name": name, "args": args, "tool_call_id": call_id}),
        "message_type": "tool_call",
    }


def tool_result(name: str, output: str, call_id: str, is_error: bool = False) -> dict:
    return {
        "role": "assistant",
        "content": json.dumps(
            {
                "name": name,
                "output": output,
                "tool_call_id": call_id,
                "is_error": is_error,
            }
        ),
        "message_type": "tool_result",
    }


def text(content: str, sender: str = "Alice", role: str = "user") -> dict:
    return {
        "role": role,
        "content": content,
        "sender_name": sender,
        "message_type": "text",
    }


def outline(messages: list[dict]) -> list[str]:
    """Render each message as ``role: block(id)`` for readable assertions."""

    def describe(block: dict) -> str:
        if "toolUse" in block:
            return f"toolUse({block['toolUse']['toolUseId']})"
        if "toolResult" in block:
            result = block["toolResult"]
            return f"toolResult({result['toolUseId']}, {result['status']})"
        return f"text({block['text']})"

    return [
        f"{message['role']}: {' '.join(describe(b) for b in message['content'])}"
        for message in messages
    ]
