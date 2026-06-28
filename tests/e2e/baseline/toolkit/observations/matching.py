"""Tolerant value matching shared by the observation collections.

Both the tool-call arg checks (``ToolCalls``) and the memory dimension checks
(``Memories``) want the same forgiving comparison: a case-insensitive substring
for text (so a paraphrased value still matches), and string-coerced equality
otherwise (so an enum like ``MemorySystem.LONG_TERM`` matches the record's
``"long_term"`` and an int ``2`` matches a JSON-stringified ``"2"``).

Deliberately substring/equality, not fuzzy (rapidfuzz/difflib) similarity: we
assert on tokens we injected and exact enum values, so we want a deterministic
"is it present" check, not a tunable score that risks matching a near-miss
(e.g. a different marker). Semantic/paraphrase checks belong to the LLM judge.
"""

from __future__ import annotations

from typing import Any


def tolerant_match(expected: Any, actual: Any) -> bool:
    """True if ``actual`` tolerantly matches ``expected``.

    ``None`` matches only ``None``. Two strings match when ``expected`` is a
    case-insensitive substring of ``actual`` (a ``StrEnum`` counts as a string, so
    ``MemorySystem.LONG_TERM`` matches ``"long_term"``); an empty ``expected``
    matches only an empty string, never everything. Everything else matches by
    equality, with a string-coerced fallback (so ``2`` matches ``"2"``).
    """
    match (expected, actual):
        case (None, None):
            return True
        case (_, None):
            return False
        case ("", _):
            # An empty pattern must not match every string -- only an empty one.
            return actual == ""
        case (str() as want, str() as have):
            return want.lower() in have.lower()
        case _:
            return expected == actual or str(expected) == str(actual)
