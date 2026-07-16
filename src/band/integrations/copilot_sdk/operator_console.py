"""Terminal operator console for Copilot's ``ask_user`` tool.

A handler for ``CopilotSDKAdapterConfig(ask_user=...)`` that answers from
the terminal of whoever runs the agent process: the model's question
renders as a prompt, the operator's typed line becomes the answer. Use it
when the answering human is the process operator; when the answering
human is in the Band room, use ``ask_user="room"`` instead (see
``room_ask_user``). Built to stay correct under the failure
modes a naive ``input()`` handler hits in a multi-room agent — each one
grounded in how the Copilot SDK actually behaves:

- **Buried prompts** — every root-logger handler is wrapped in a
  :class:`LogGate`; while a question is open, log records are parked and
  re-emitted after the answer, so output never interleaves with typing.
- **Concurrent rooms** — the SDK dispatches each ``userInput.request``
  as its own task with no serialization, so questions from different
  rooms can arrive simultaneously. They queue on a lock here; one prompt
  owns the terminal at a time.
- **Abandoned prompts** — the SDK never cancels a pending question (not
  even on session abort) and has no handler timeout, so a blocked
  ``input()`` would leak forever and its eventual line would answer the
  wrong room's question. A single persistent reader owns stdin instead:
  answers pair only with the question currently displayed, and lines
  typed while no question is open are discarded.
- **Slow operators** — each question carries a deadline
  (``answer_timeout_s``). On expiry the model gets an explicit "operator
  did not answer" reply instead of the room's whole turn dying on the
  adapter's ``turn_timeout_s``. Keep ``answer_timeout_s`` *below* the
  adapter's turn timeout, or the turn dies first and the answer is lost.
- **Invalid answers** — the SDK forwards the response verbatim without
  checking it against ``choices``/``allowFreeform``, so enforcement
  happens here: choice lists accept a number or exact choice text, and
  freeform text is re-prompted when the request forbids it.
- **Headless runs** — on stdin EOF (piped input exhausted, no terminal)
  every question immediately answers "operator unavailable" instead of
  crashing the turn (a raised error would surface to the runtime as an
  opaque RPC failure).

Multiple agents in one process must **share one console instance** — it
owns process-wide resources (stdin, the root logger), so per-agent
instances would fight over both.

The module depends only on the wire *shape* of the Copilot SDK's
``UserInputRequest``/``UserInputResponse`` TypedDicts (plain dicts at
runtime), so it imports nothing from ``copilot``.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import sys
import threading
from collections.abc import Iterator
from typing import IO, Any

logger = logging.getLogger(__name__)

NO_ANSWER_TEXT = "(The operator did not answer in time — proceed without them.)"
UNAVAILABLE_TEXT = "(No operator is attached to this agent — proceed without them.)"


class LogGate(logging.Handler):
    """Wraps a real log handler so the console can pause its output.

    While paused, records are parked instead of written; the matching
    ``resume()`` re-emits them in order. Pauses are counted, so gates
    shared by several consoles stay paused until every open prompt has
    resumed. Log lines are deferred — never lost — and can never bury an
    open operator prompt.

    The gate snapshots the target's *level* at wrap time and delegates
    records to the target's own formatter; reconfigure logging before
    gating (or re-run ``gate_logging``) rather than mutating a wrapped
    handler afterwards.
    """

    def __init__(self, target: logging.Handler) -> None:
        super().__init__(target.level)
        self._target = target
        self._parked: list[logging.LogRecord] = []
        self._pauses = 0

    def emit(self, record: logging.LogRecord) -> None:
        # Handler.handle() already holds this handler's lock here.
        if self._pauses:
            self._parked.append(record)
        else:
            self._target.handle(record)

    def flush(self) -> None:
        self._target.flush()

    def close(self) -> None:
        self._target.close()
        super().close()

    def pause(self) -> None:
        self.acquire()
        try:
            self._pauses += 1
        finally:
            self.release()

    def resume(self) -> None:
        # The backlog flushes under the gate lock: records emitted from
        # other threads meanwhile queue behind it instead of jumping it.
        self.acquire()
        try:
            self._pauses = max(0, self._pauses - 1)
            if self._pauses == 0:
                parked, self._parked = self._parked, []
                for record in parked:
                    self._target.handle(record)
        finally:
            self.release()


class OperatorConsole:
    """Owns the terminal for operator Q&A; ``ask`` is the adapter handler.

    Pass ``ask`` as ``CopilotSDKAdapterConfig(ask_user=...)``; log gating
    engages automatically on the first question.

    Args:
        answer_timeout_s: Seconds a question waits for the operator before
            answering :data:`NO_ANSWER_TEXT` on their behalf. Must stay
            below the adapter's ``turn_timeout_s`` or the turn dies first
            and the answer is lost — the default deliberately sits under
            the adapter's default 120s so the out-of-the-box pairing
            degrades gracefully; raise BOTH for patient operators (e.g.
            ``answer_timeout_s=300`` with ``turn_timeout_s=600``).
        input_stream: Where operator answers are read from (stdin by
            default; primarily a test seam).
    """

    def __init__(
        self,
        *,
        answer_timeout_s: float = 90.0,
        input_stream: IO[str] | None = None,
    ) -> None:
        self.answer_timeout_s = answer_timeout_s
        self._input = input_stream if input_stream is not None else sys.stdin
        self._lock = asyncio.Lock()
        self._gates: list[LogGate] = []
        # A plain thread queue, not an asyncio one: the reader thread must
        # outlive any single event loop (agents may be restarted on a new
        # loop while the thread is still blocked on the terminal).
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._eof = False

    def gate_logging(self) -> None:
        """Route every root-logger handler through a pausable gate.

        Runs automatically on the first question, when logging is
        guaranteed to be configured; call it explicitly only to adopt the
        root handlers at a specific point in setup.

        Idempotent: an already-gated root logger is not re-wrapped — the
        existing gates are adopted instead, so a second console sharing
        the process still parks logs while its prompts are open.
        """
        root = logging.getLogger()
        gates = [h for h in root.handlers if isinstance(h, LogGate)]
        if not gates:
            gates = [LogGate(handler) for handler in root.handlers]
            root.handlers[:] = gates
        self._gates = gates

    async def ask(
        self, request: dict[str, Any], context: dict[str, str]
    ) -> dict[str, Any]:
        """Render the model's question and read the operator's answer.

        A numeric answer picks the matching choice; other text is passed
        through as freeform (when the request allows it) or re-prompted.
        """
        self._ensure_reader()
        if not self._gates:
            self.gate_logging()
        async with self._lock:
            for gate in self._gates:
                gate.pause()
            try:
                return await self._prompt_operator(request, context)
            finally:
                for gate in self._gates:
                    gate.resume()

    async def _prompt_operator(
        self, request: dict[str, Any], context: dict[str, str]
    ) -> dict[str, Any]:
        """Run one prompt cycle under the console lock with gates paused."""
        self._discard_unsolicited_lines()
        deadline = asyncio.get_running_loop().time() + self.answer_timeout_s
        self._write(self._render(request, context))
        while True:
            answer = await self._read_line(deadline)
            if answer is None:
                self._write("(no answer — replying on the operator's behalf)\n")
                text = UNAVAILABLE_TEXT if self._eof else NO_ANSWER_TEXT
                return {"answer": text, "wasFreeform": True}
            response = self._match_answer(answer, request)
            if response is not None:
                return response
            self._write(self._reprompt_text(request))

    def _ensure_reader(self) -> None:
        """Start the input reader thread once.

        The thread talks only to the thread-safe line queue — never to an
        event loop — so it survives agent/loop restarts and keeps serving
        whichever loop is asking.
        """
        if self._reader is not None or self._eof:
            return
        source = self._input
        lines = self._lines

        def read_lines() -> None:
            for line in source:
                lines.put(line)
            lines.put(None)

        # Daemon: a blocked readline must never hold up process exit.
        self._reader = threading.Thread(
            target=read_lines, name="operator-console-input", daemon=True
        )
        self._reader.start()

    async def _read_line(self, deadline: float) -> str | None:
        """Return the next typed line, or None on timeout/EOF."""
        if self._eof:
            return None
        remaining = deadline - asyncio.get_running_loop().time()
        try:
            if remaining <= 0:
                # Deadline hit: still honor an answer that already arrived.
                line = self._lines.get_nowait()
            else:
                # The timeout goes to Queue.get so it bounds the worker
                # THREAD; a timed-out read leaves no thread blocked to eat
                # the next typed line. Do NOT swap this for
                # wait_for(to_thread(get)) — that cancels only the await
                # and leaks the blocked thread.
                line = await asyncio.to_thread(self._lines.get, timeout=remaining)
        except queue.Empty:
            return None
        return self._line_or_eof(line)

    def _line_or_eof(self, line: str | None) -> str | None:
        """Strip a real line; latch EOF and return None on the sentinel."""
        if line is None:
            self._eof = True
            return None
        return line.strip()

    def _drain_buffered_lines(self) -> Iterator[str | None]:
        """Yield every line currently queued, emptying it without blocking."""
        while True:
            try:
                yield self._lines.get_nowait()
            except queue.Empty:
                return

    def _discard_unsolicited_lines(self) -> None:
        """Drop lines typed while no question was open.

        They answered nothing — most likely an operator hitting enter on
        a prompt that had already expired — and must not leak into the
        next question. An EOF sentinel found here is honored, not dropped.
        """
        dropped = sum(
            self._line_or_eof(line) is not None for line in self._drain_buffered_lines()
        )
        if dropped:
            self._write(
                f"(ignored {dropped} line(s) typed while no question was open)\n"
            )

    @staticmethod
    def _choices(request: dict[str, Any]) -> list[str]:
        return request.get("choices") or []

    def _match_answer(
        self, answer: str, request: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Map a typed line to a response, or None to re-prompt.

        Accepts a choice number or exact choice text; freeform text is
        accepted only when the request allows it. Empty input re-prompts.
        """
        choices = self._choices(request)
        matched = [c for c in choices if c.casefold() == answer.casefold()]
        # isdecimal, not isdigit: isdigit also accepts characters (e.g.
        # superscripts) that int() rejects with a ValueError.
        if answer.isdecimal() and choices:
            index = int(answer)
            if 1 <= index <= len(choices):
                return {"answer": choices[index - 1], "wasFreeform": False}
            if matched:  # a numeric-labeled choice typed by its label
                return {"answer": matched[0], "wasFreeform": False}
            # A bare number against a numbered list signals choice-selection
            # intent; an out-of-range one is a typo, not a freeform answer.
            return None
        if matched:
            return {"answer": matched[0], "wasFreeform": False}
        freeform_ok = request.get("allowFreeform", True) or not choices
        if answer and freeform_ok:
            return {"answer": answer, "wasFreeform": True}
        return None

    @classmethod
    def _answer_hint(cls, request: dict[str, Any]) -> str:
        """The accepted-answer shapes, honoring ``allowFreeform``."""
        choices = cls._choices(request)
        if not choices:
            return "text"
        numbers = f"1-{len(choices)}"
        return f"{numbers} or text" if request.get("allowFreeform", True) else numbers

    @classmethod
    def _reprompt_text(cls, request: dict[str, Any]) -> str:
        return f"Please answer [{cls._answer_hint(request)}]: "

    @classmethod
    def _render(cls, request: dict[str, Any], context: dict[str, str]) -> str:
        choices = cls._choices(request)
        return "\n".join(
            [
                "",
                "-" * 60,
                f"Copilot asks (session {context.get('session_id', '?')}):",
                f"  {request.get('question', '')}",
                *(f"    {i}) {choice}" for i, choice in enumerate(choices, 1)),
                f"Answer [{cls._answer_hint(request)}]: ",
            ]
        )

    @staticmethod
    def _write(text: str) -> None:
        """Write console UI text straight to stdout (this *is* the UI).

        Write failures (e.g. stdout is a pipe whose reader closed) are
        downgraded to a log warning: a broken display must degrade to the
        timeout/EOF answer path, not blow up the turn as an RPC error.
        """
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        # ValueError: a fully CLOSED stdout (not just a broken pipe) raises
        # "I/O operation on closed file", which is not an OSError.
        except (OSError, ValueError) as exc:
            logger.warning("Operator console cannot write to stdout: %s", exc)
