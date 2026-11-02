"""A SessionDecider over a real Session, recording what it reported."""

from __future__ import annotations

from band_sdk_core import Session, SessionPolicy
from phoenix_channels_python_client import (
    Connected,
    ConnectFailed,
    Connecting,
    Disconnected,
    ReconnectDecision,
)
from websockets.exceptions import InvalidStatus

from band.client.streaming import SupersedePayload, WebSocketDisconnectReason
from band.client.streaming.session_decider import (
    PROCEED,
    SessionDecider,
    SupersedeVerdict,
)
from tests.conftest import RETRYABLE_SUPERSEDE, TERMINAL_SUPERSEDE
from tests.websocket.client.kit import upgrade_error_body, upgrade_exception

SECRET = "synthetic-decider-secret"
REDACTED = "[redacted]"
TERMINAL = SupersedePayload.model_validate(TERMINAL_SUPERSEDE)
RETRYABLE = SupersedePayload.model_validate(RETRYABLE_SUPERSEDE)


def redact(text: str) -> str:
    return text.replace(SECRET, REDACTED)


def refusal(
    status: int, *, code: str = "refused", headers: dict[str, str] | None = None
) -> InvalidStatus:
    """A refused handshake as websockets raises it."""
    return upgrade_exception(status, upgrade_error_body(code, status=status), headers)


class DeciderProbe:
    """Feeds events to one SessionDecider and records its callbacks."""

    def __init__(self, policy: SessionPolicy) -> None:
        self.reported: list[WebSocketDisconnectReason] = []
        self.first_connections = 0
        self.decider = SessionDecider(
            session=Session(policy),
            redact=redact,
            on_terminal=self.reported.append,
            on_first_connected=self._first_connected,
        )
        self.generation = 0

    def _first_connected(self) -> None:
        self.first_connections += 1

    def attempt(self) -> ReconnectDecision:
        return self.decider(Connecting())

    def connected(self) -> ReconnectDecision:
        self.generation += 1
        return self.decider(Connected(self.generation))

    def connect(self) -> None:
        """Begin an attempt whose socket comes up, as phoenix asks it."""
        if (self.attempt(), self.connected()) != (PROCEED, PROCEED):
            raise AssertionError("The session refused a healthy connection")

    def closed(self, close_code: int | None) -> ReconnectDecision:
        return self.decider(Disconnected(close_code=close_code, close_reason=""))

    def failed(self, error: Exception) -> ReconnectDecision:
        return self.decider(ConnectFailed(error))

    def supersede(self, payload: SupersedePayload) -> SupersedeVerdict:
        return self.decider.supersede(payload)


Verdict = tuple[bool, float, str | None]


def verdict(decision: ReconnectDecision) -> Verdict:
    """Whether the client goes on, after what delay, and with which error."""
    error = decision.terminal_error
    return (
        decision.should_reconnect,
        decision.delay_s,
        str(error) if error is not None else None,
    )


PROCEEDS: Verdict = (True, 0.0, None)
STOPS: Verdict = (False, 0.0, None)
