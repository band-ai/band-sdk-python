"""The platform's own definition of visible content."""

from __future__ import annotations

import unicodedata

# Mirrors the platform's own rule for what counts as content, rather than a
# plain non-empty check: whitespace-only, zero-width, and bidi-mark-only
# strings pass a naive `len(content) > 0` test but the platform still rejects
# them with "can't be blank" (thenvoi-platform
# lib/thenvoi_com/thenvoi/chat/chat_message.ex `validate_has_visible_content/2`,
# delegating to `Chat.validate_visible_content/1`, whose regex lives at
# lib/thenvoi_com/thenvoi/chat.ex:3936 -- `~r/[\\p{L}\\p{N}\\p{P}\\p{S}]/u`).
# Every letter/number/punctuation/symbol counts as visible; every other
# Unicode category (whitespace, control, formatting, marks) does not.
_VISIBLE_CATEGORY_PREFIXES = ("L", "N", "P", "S")


def has_visible_content(value: str) -> bool:
    """Return whether *value* has at least one visible character."""
    return any(
        unicodedata.category(char)[0] in _VISIBLE_CATEGORY_PREFIXES for char in value
    )


# Matches the platform's Ecto changeset error for a blank `:content` field, so
# a caller sees the same wording whether the platform rejected the request or
# the SDK caught it first.
BLANK_CONTENT_ERROR = "can't be blank"
