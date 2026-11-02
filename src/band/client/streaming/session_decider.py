"""Every connect, reconnect and stop decision for one WebSocket session.

``SessionDecider`` is phoenix's ``reconnect_decider``: it maps each transport
event onto band-sdk-core's ``Session`` and returns that verdict, so the
session has one decision owner from the first attempt on.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from enum import StrEnum
from typing import TYPE_CHECKING

from band_sdk_core import DeadReason, Session, SessionOutcome, SessionState
from phoenix_channels_python_client import (
    Connected,
    ConnectFailed,
    Connecting,
    Disconnected,
    ReconnectDecision,
    ReconnectEvent,
)
from phoenix_channels_python_client.client_types import PROCEED
from phoenix_channels_python_client.exceptions import PHXConnectionError

from band.client.streaming.disconnect import (
    TerminalReason,
    WebSocketDisconnectReason,
    refusal_reason,
    session_reason,
)
from band.client.streaming.errors import WebSocketUpgradeError

if TYPE_CHECKING:
    from band.client.streaming.client import SupersedePayload

logger = logging.getLogger(__name__)

STOP = ReconnectDecision(should_reconnect=False)


class SupersedeVerdict(StrEnum):
    """What a supersede push does to the session."""

    STALE = "stale"
    RETRY = "retry"
    END = "end"
    ALREADY_ENDED = "already_ended"


class SessionDecider:
    """Drive one ``Session`` with phoenix's decision events.

    Decisions run synchronously on phoenix's supervisor task and ``supersede``
    on a topic task, all on one loop with no await inside, so they never
    interleave.
    """

    def __init__(
        self,
        *,
        session: Session,
        redact: Callable[[str], str],
        on_terminal: Callable[[WebSocketDisconnectReason], None],
        on_first_connected: Callable[[], None],
    ) -> None:
        self._session = session
        self._redact = redact
        self._on_terminal = on_terminal
        self._on_first_connected = on_first_connected
        self._epoch: int | None = None
        self._accepted = False
        self._ended = False
        self._superseded: SessionOutcome | None = None
        self._refusal: WebSocketUpgradeError | None = None
        self.terminal: WebSocketDisconnectReason | None = None

    @property
    def terminal_refusal(self) -> WebSocketUpgradeError | None:
        """The refused upgrade that ended the session, if one did."""
        return self._refusal if self.terminal is not None else None

    def __call__(self, event: ReconnectEvent) -> ReconnectDecision:
        match event:
            case Connecting():
                return self._begin_attempt()
            case Connected():
                return self._connected()
            case Disconnected() | ConnectFailed() if self._superseded is not None:
                # The supersede retired this attempt's epoch; its delay applies.
                return self._retry(self._superseded, "WebSocket superseded")
            case Disconnected(close_code=close_code):
                return self._closed(close_code)
            case ConnectFailed(error=error):
                return self._failed(error)

    def supersede(self, payload: SupersedePayload) -> SupersedeVerdict:
        """Record a supersede push; the next decision applies it."""
        outcome = self._session.on_supersede(
            _now(), payload.retryable, payload.retry_after, random.random()
        )
        if outcome.stale_reason is not None:
            logger.info("Ignoring a stale supersede (%s)", outcome.stale_reason)
            return SupersedeVerdict.STALE
        if outcome.state is not SessionState.Dead:
            self._superseded = outcome
            return SupersedeVerdict.RETRY
        if self._decided:
            return SupersedeVerdict.ALREADY_ENDED
        self._report(payload.to_disconnect_reason(outcome))
        return SupersedeVerdict.END

    def end(self) -> None:
        """End the session locally; later verdicts report nothing."""
        self._ended = True
        self._session.end()

    def _begin_attempt(self) -> ReconnectDecision:
        self._superseded = None
        self._refusal = None
        epoch = self._session.begin_attempt(_now())
        if epoch is None:
            return STOP
        self._epoch = epoch
        return PROCEED

    def _connected(self) -> ReconnectDecision:
        outcome = self._session.on_connected(self._live_epoch, _now())
        if outcome.stale_reason is not None or outcome.state is not SessionState.Up:
            return self._end(_stale_connection_reason(outcome))
        if not self._accepted:
            self._accepted = True
            self._on_first_connected()
        return PROCEED

    def _closed(self, close_code: int | None) -> ReconnectDecision:
        outcome = self._session.on_socket_close(
            self._live_epoch, _now(), close_code, random.random()
        )
        if outcome.state is SessionState.Dead:
            return self._end(_closed_reason(close_code, outcome))
        return self._retry(outcome, f"WebSocket closed (code={close_code})")

    def _failed(self, error: Exception) -> ReconnectDecision:
        refusal = self._refusal = WebSocketUpgradeError.from_exception(
            error, redact=self._redact
        )
        if refusal is None:
            return self._connect_failed(type(error).__name__)
        outcome = self._session.on_upgrade_rejected(
            self._live_epoch,
            _now(),
            refusal.status_code,
            refusal.retry_after,
            random.random(),
        )
        if outcome.state is SessionState.Dead:
            return self._end(refusal_reason(refusal, outcome))
        return self._retry(
            outcome, f"WebSocket upgrade refused ({refusal.status_code}: {refusal})"
        )

    def _connect_failed(self, error_name: str) -> ReconnectDecision:
        outcome = self._session.on_socket_close(
            self._live_epoch, _now(), None, random.random()
        )
        message = f"WebSocket connect failed ({error_name})"
        if outcome.state is SessionState.Dead:
            return self._end(
                session_reason(TerminalReason.CONNECTION_FAILED, message, outcome)
            )
        return self._retry(outcome, message)

    def _retry(self, outcome: SessionOutcome, detail: str) -> ReconnectDecision:
        delay_s = outcome.retry_after_s
        if delay_s is None:
            raise RuntimeError(f"Session returned no retry delay for {outcome.state}")
        logger.warning("%s; retrying in %.2fs", detail, delay_s)
        return ReconnectDecision(should_reconnect=True, delay_s=delay_s)

    def _end(self, reason: WebSocketDisconnectReason) -> ReconnectDecision:
        if self._decided:
            return STOP
        self._report(reason)
        # No cause chained: a raw upgrade response can retain credentials.
        return ReconnectDecision(
            should_reconnect=False, terminal_error=PHXConnectionError(reason.message)
        )

    def _report(self, reason: WebSocketDisconnectReason) -> None:
        self.terminal = reason
        if self._accepted:
            self._on_terminal(reason)
            return
        # The caller gets only the raised error, which drops the session's reason.
        logger.warning(
            "WebSocket session ended before connecting: %s (%s): %s",
            reason.reason,
            reason.dead_reason,
            reason.message,
        )

    @property
    def _decided(self) -> bool:
        """The session already has its verdict, or ended locally."""
        return self.terminal is not None or self._ended

    @property
    def _live_epoch(self) -> int:
        if self._epoch is None:
            raise RuntimeError("A connection event arrived before any attempt began")
        return self._epoch


def _now() -> float:
    return asyncio.get_running_loop().time()


def _stale_connection_reason(outcome: SessionOutcome) -> WebSocketDisconnectReason:
    return session_reason(
        TerminalReason.STALE_CONNECTION,
        "WebSocket connected after its session moved on",
        outcome,
    )


def _closed_reason(
    close_code: int | None, outcome: SessionOutcome
) -> WebSocketDisconnectReason:
    if outcome.dead_reason is DeadReason.RapidDisconnect:
        return session_reason(
            TerminalReason.RAPID_DISCONNECT,
            "WebSocket kept disconnecting right after connecting",
            outcome,
        )
    return session_reason(
        TerminalReason.SOCKET_CLOSED,
        f"WebSocket closed with code {close_code}",
        outcome,
    )
