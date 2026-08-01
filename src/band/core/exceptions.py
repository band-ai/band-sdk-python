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
    ) -> BandConfigError:
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


class MissingDependencyError(BandConfigError, ImportError):
    """Optional package missing. Also an ``ImportError`` for legacy ``except`` clauses."""


class UnsupportedOptionError(BandConfigError):
    """Caller passed an option the backend/provider does not support."""


class DuplicateToolError(BandConfigError):
    """Two tools registered under the same name."""


class BandConnectionError(BandError):
    """Transport failures (WebSocket, REST). Actionable by ops."""


class BandToolError(BandError):
    """Tool execution failures. Actionable by adapter/LLM."""


class LifecycleError(BandError):
    """Invalid gateway/backend lifecycle transition."""


class MaxToolRoundsExceeded(BandError, RuntimeError):
    """A turn asked for tools on every round without producing a final answer.

    Also a ``RuntimeError`` so callers that only knew the old untyped raise
    keep working.
    """


class RunFailed(BandError):
    """Model or execution failure surfaced on an ``AgentStream``.

    Yielded as a ``RunFailedEvent`` on the observation stream (does not abort
    iteration by itself). Transport failures raise ``StreamError`` instead.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        partial_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.partial_text = partial_text


class StreamError(BandError):
    """Transport failure while consuming an ``AgentStream``.

    Distinct from ``RunFailed``: model/exec failures are yielded as events;
    transport failures abort iteration by raising.
    """


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
