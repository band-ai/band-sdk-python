"""The text of a google-genai conversation, for the Google adapters' probes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def conversation_text(contents: Iterable[Any]) -> str:
    """The text of every part of ``contents``, in order, one part per line."""
    return "\n".join(
        part.text for content in contents for part in content.parts or [] if part.text
    )
