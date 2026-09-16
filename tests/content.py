"""Shared content test vectors.

One list of the strings the platform counts as blank, so every test that
asserts a blank-content refusal covers the same cases -- including the ones
a naive non-empty check waves through (whitespace, zero-width).
``band.core.content.has_visible_content`` is the rule under test; see
``tests/core/test_content.py`` for its full category table.
"""

from __future__ import annotations

BLANK_CONTENT_CASES = ["", "   ", "\n\t ", "\u200b"]
