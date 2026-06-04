"""Band SDK exception hierarchy."""

from __future__ import annotations

from collections.abc import Iterable


class BandError(Exception):
    """Base for all SDK exceptions."""


class BandConfigError(BandError):
    """Configuration or setup problems. Actionable by developer.

    Use ``with_suggestion`` to attach a "did you mean?" hint when the
    user likely typoed a known parameter or capability name.
    """

    @classmethod
    def with_suggestion(
        cls,
        message: str,
        bad_name: str,
        valid_names: Iterable[str],
        *,
        max_distance: int = 2,
    ) -> "BandConfigError":
        """Build an error message with a typo suggestion if one is close enough.

        Args:
            message: Base error message.
            bad_name: The unknown / misspelled name the user supplied.
            valid_names: Known-good names to compare against.
            max_distance: Maximum Levenshtein distance to consider a match
                (default 2 — catches single-char typos and small swaps).
        """
        suggestion = _closest_match(bad_name, valid_names, max_distance=max_distance)
        if suggestion is not None:
            return cls(f"{message} Did you mean {suggestion!r}?")
        return cls(message)


class BandConnectionError(BandError):
    """Transport failures (WebSocket, REST). Actionable by ops."""


class BandToolError(BandError):
    """Tool execution failures. Actionable by adapter/LLM."""


def _levenshtein(a: str, b: str) -> int:
    """Iterative Levenshtein distance. Pure Python, no dependencies."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            insert = current[j - 1] + 1
            delete = previous[j] + 1
            substitute = previous[j - 1] + (0 if ca == cb else 1)
            current[j] = min(insert, delete, substitute)
        previous = current
    return previous[-1]


def _closest_match(
    needle: str,
    haystack: Iterable[str],
    *,
    max_distance: int = 2,
) -> str | None:
    """Return the closest name from haystack within max_distance, or None."""
    needle_lower = needle.lower()
    best: tuple[int, str] | None = None
    for candidate in haystack:
        distance = _levenshtein(needle_lower, candidate.lower())
        if distance > max_distance:
            continue
        if best is None or distance < best[0]:
            best = (distance, candidate)
    return best[1] if best is not None else None
