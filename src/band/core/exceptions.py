"""Band SDK exception hierarchy."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from band.client.streaming.client import WebSocketDisconnectReason


class BandError(Exception):
    """Base for all SDK exceptions."""


class BandConfigError(BandError):
    """Configuration, setup or lifecycle misuse. Actionable by developer.

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


class AgentAlreadyRunningError(BandConfigError):
    """A second instance of this agent id was refused, by the host lock
    (``AgentConfig.single_instance``) or the platform
    (``AgentConfig.conflict_policy``)."""


class BandConnectionError(BandError):
    """Transport failures (WebSocket, REST). Actionable by ops."""


class AgentDisconnectedError(BandConnectionError):
    """The platform ended this agent's connection for good.

    ``reason`` is the typed terminal disconnect cause. A host should not
    blindly restart a superseded identity and fight the other connection.
    """

    def __init__(self, reason: WebSocketDisconnectReason) -> None:
        super().__init__(
            f"Platform disconnected the agent: {reason.reason} ({reason.message})"
        )
        self.reason = reason


class BandToolError(BandError):
    """Tool execution failures. Actionable by adapter/LLM."""


class MessagePostKind(StrEnum):
    """A chat-message post or delivery-receipt failure.

    ``unconfirmed_recipient`` is an HTTP 2xx whose recipient has no handle:
    the message was delivered, but its receipt could not be parsed.
    ``mentioned_participant_not_in_room`` is the platform's own refusal token
    for a mention that is not in the room.
    """

    UNCONFIRMED_RECIPIENT = "unconfirmed_recipient"
    MENTION_NOT_IN_ROOM = "mentioned_participant_not_in_room"


class MessagePostError(BandToolError):
    """A classified chat-message post failure.

    ``kind`` is the stable token logs and tool results carry. ``detail`` is
    the short header-free origin. A caller that rebuilds the error from its
    message string leaves both unset.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: MessagePostKind | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.detail = detail

    @classmethod
    def classified(cls, kind: MessagePostKind, detail: str) -> MessagePostError:
        return cls(f"{kind}: {detail}", kind=kind, detail=detail)


class InvalidToolArgumentsError(BandError, ValueError):
    """A tool call's arguments failed its input model. Actionable by the LLM,
    which reads the message and retries; a ``ValueError`` for callers that
    already catch one."""


class InputNotAccepted(BandError):
    """This attempt provably started no provider execution.

    Transport failures, timeouts and uncertain acceptance do not qualify.
    The host retains the input without reporting a failed turn.
    """


class RoomClosingError(InputNotAccepted, RuntimeError):
    """A closing room, or a stopping residency owner, refused work before it
    started. Actionable by the host as ``InputNotAccepted``; distinct from a
    cleanup failure, which the same calls raise as other errors."""


class ProviderRunError(BandError):
    """A provider or framework returned a failed run as data instead of raising.
    Actionable by the adapter: raise it inside the turn so the turn is reported
    failed.

    ``code`` is coarse and safe to post; ``detail`` is provider text, which can
    echo the prompt, so it belongs only in logs.
    """

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class RoomExecutionStoppedError(BandError):
    """The platform refused a claim, acknowledgement, or post while stopped.

    The refusal describes that request, not the room's current execution state.
    """

    def __init__(self, room_id: str) -> None:
        super().__init__(f"Room {room_id}: platform execution was stopped")
        self.room_id = room_id


class CleanupIncompleteError(BandError):
    """Owned work or resources outlived a cleanup attempt. Actionable by the
    host: the owner keeps them and retries settlement on the next stop."""


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
