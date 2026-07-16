"""Tests for the ask_user operator console.

The console reads answers from an injected input stream; tests drive it
through an ``os.pipe`` so lines arrive exactly when written and EOF is
explicit (closing the write end), mirroring real terminal behavior.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Iterator

import pytest

from band.integrations.copilot_sdk.operator_console import (
    NO_ANSWER_TEXT,
    UNAVAILABLE_TEXT,
    LogGate,
    OperatorConsole,
)

CHOICES_REQUEST = {
    "question": "Which channel?",
    "choices": ["stable", "beta", "canary"],
    "allowFreeform": True,
}
CONTEXT = {"session_id": "band-agent-room-1"}


class PipeOperator:
    """Test double for the human: writes lines into the console's stdin."""

    def __init__(self) -> None:
        read_fd, self._write_fd = os.pipe()
        self.stream = os.fdopen(read_fd, "r")

    def types(self, line: str) -> None:
        os.write(self._write_fd, f"{line}\n".encode())

    def leaves(self) -> None:
        """Close the write end — the console sees EOF."""
        os.close(self._write_fd)


@pytest.fixture(autouse=True)
def pristine_root_handlers() -> Iterator[None]:
    """Undo the console's (deliberate) root-handler gating after each test.

    ``ask()`` gates root logging lazily; leaked gates would wrap pytest's
    own handlers and double-count records in later log-asserting tests.
    """
    root = logging.getLogger()
    saved = root.handlers[:]
    yield
    root.handlers[:] = saved


@pytest.fixture
def operator() -> Iterator[PipeOperator]:
    op = PipeOperator()
    yield op
    try:
        op.leaves()
    except OSError:
        pass  # already closed by the test
    # The reader thread exits on the EOF above; close the read end too so
    # fds don't accumulate across the suite.
    op.stream.close()


def make_console(operator: PipeOperator, **kwargs) -> OperatorConsole:
    kwargs.setdefault("answer_timeout_s", 5.0)
    return OperatorConsole(input_stream=operator.stream, **kwargs)


async def ask_after_prompt(
    console: OperatorConsole,
    operator: PipeOperator,
    lines: list[str],
    request: dict | None = None,
    capsys=None,
) -> dict:
    """Start an ask and type ``lines`` once the prompt has rendered."""
    task = asyncio.create_task(console.ask(request or CHOICES_REQUEST, CONTEXT))
    await asyncio.sleep(0.05)  # let the prompt render and the reader start
    for line in lines:
        operator.types(line)
    return await asyncio.wait_for(task, timeout=5)


class TestAnswerMatching:
    @pytest.mark.asyncio
    async def test_numeric_answer_picks_choice(self, operator):
        console = make_console(operator)
        result = await ask_after_prompt(console, operator, ["2"])
        assert result == {"answer": "beta", "wasFreeform": False}

    @pytest.mark.asyncio
    async def test_exact_choice_text_matches_case_insensitively(self, operator):
        console = make_console(operator)
        result = await ask_after_prompt(console, operator, ["CANARY"])
        assert result == {"answer": "canary", "wasFreeform": False}

    @pytest.mark.asyncio
    async def test_freeform_text_passes_through_when_allowed(self, operator):
        console = make_console(operator)
        result = await ask_after_prompt(console, operator, ["ship it tomorrow"])
        assert result == {"answer": "ship it tomorrow", "wasFreeform": True}

    @pytest.mark.asyncio
    async def test_freeform_rejected_when_not_allowed_then_reprompts(
        self, operator, capsys
    ):
        console = make_console(operator)
        request = {**CHOICES_REQUEST, "allowFreeform": False}
        result = await ask_after_prompt(
            console, operator, ["purple", "1"], request=request
        )
        assert result == {"answer": "stable", "wasFreeform": False}
        out = capsys.readouterr().out
        # Both the initial prompt and the reprompt must advertise only the
        # answer shapes the request accepts — no "or text" invitation.
        assert "Answer [1-3]: " in out
        assert "Please answer [1-3]: " in out
        assert "or text" not in out

    @pytest.mark.asyncio
    async def test_unicode_digit_answer_reprompts_instead_of_crashing(self, operator):
        """'²' is isdigit()-true but int()-invalid; it must re-prompt, not
        blow up the ask_user RPC with a ValueError."""
        console = make_console(operator)
        request = {**CHOICES_REQUEST, "allowFreeform": False}
        result = await ask_after_prompt(console, operator, ["²", "2"], request=request)
        assert result == {"answer": "beta", "wasFreeform": False}

    @pytest.mark.asyncio
    async def test_out_of_range_number_reprompts_not_freeform(self, operator):
        """A bare number against a numbered list signals choice intent; a
        typo like '5' must re-prompt, not pass through as freeform text."""
        console = make_console(operator)
        result = await ask_after_prompt(console, operator, ["5", "2"])
        assert result == {"answer": "beta", "wasFreeform": False}

    @pytest.mark.asyncio
    async def test_numeric_choice_label_matches_by_text(self, operator):
        """Numeric-labeled choices (ports, years) stay selectable by label;
        positional numbers still win inside the 1..N range."""
        console = make_console(operator)
        request = {"question": "Which port?", "choices": ["8080", "9090"]}
        result = await ask_after_prompt(console, operator, ["9090"], request)
        assert result == {"answer": "9090", "wasFreeform": False}

    @pytest.mark.asyncio
    async def test_empty_answer_reprompts(self, operator):
        console = make_console(operator)
        result = await ask_after_prompt(console, operator, ["", "3"])
        assert result == {"answer": "canary", "wasFreeform": False}

    @pytest.mark.asyncio
    async def test_question_without_choices_takes_any_text(self, operator):
        console = make_console(operator)
        request = {"question": "Deploy notes?", "allowFreeform": False}
        result = await ask_after_prompt(console, operator, ["all clear"], request)
        assert result == {"answer": "all clear", "wasFreeform": True}


class TestTimeoutsAndEof:
    @pytest.mark.asyncio
    async def test_unanswered_question_expires_with_explicit_text(self, operator):
        console = make_console(operator, answer_timeout_s=0.1)
        result = await asyncio.wait_for(
            console.ask(CHOICES_REQUEST, CONTEXT), timeout=5
        )
        assert result == {"answer": NO_ANSWER_TEXT, "wasFreeform": True}

    @pytest.mark.asyncio
    async def test_eof_answers_operator_unavailable(self, operator):
        console = make_console(operator)
        operator.leaves()
        result = await asyncio.wait_for(
            console.ask(CHOICES_REQUEST, CONTEXT), timeout=5
        )
        assert result == {"answer": UNAVAILABLE_TEXT, "wasFreeform": True}
        # Subsequent questions fail fast instead of waiting out the timeout.
        started = time.monotonic()
        result = await asyncio.wait_for(
            console.ask(CHOICES_REQUEST, CONTEXT), timeout=5
        )
        assert result == {"answer": UNAVAILABLE_TEXT, "wasFreeform": True}
        assert time.monotonic() - started < 1

    @pytest.mark.asyncio
    async def test_line_typed_between_questions_is_discarded(self, operator, capsys):
        """A late answer to an expired prompt must not answer the next one."""
        console = make_console(operator)
        task = asyncio.create_task(console.ask(CHOICES_REQUEST, CONTEXT))
        await asyncio.sleep(0.05)
        operator.types("2")
        assert (await task)["answer"] == "beta"

        operator.types("1")  # stray line: no question is open
        await asyncio.sleep(0.1)  # let it reach the queue
        result = await ask_after_prompt(console, operator, ["3"])
        assert result == {"answer": "canary", "wasFreeform": False}
        assert "ignored 1 line(s)" in capsys.readouterr().out


class TestLoopRestart:
    def test_console_survives_event_loop_restart(self, operator):
        """One console must keep serving after its first event loop closes.

        Agents can be stopped and restarted on a fresh loop while the
        reader thread is still blocked on the terminal; the console must
        not stay bound to the dead loop.
        """
        console = make_console(operator)

        async def first_loop() -> dict:
            return await ask_after_prompt(console, operator, ["1"])

        async def second_loop() -> dict:
            return await ask_after_prompt(console, operator, ["2"])

        assert asyncio.run(first_loop())["answer"] == "stable"
        assert asyncio.run(second_loop())["answer"] == "beta"


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_questions_serialize_and_get_own_answers(self, operator):
        """Two rooms asking at once: prompts never interleave, answers pair up."""
        console = make_console(operator)
        first = asyncio.create_task(console.ask(CHOICES_REQUEST, CONTEXT))
        second = asyncio.create_task(
            console.ask(CHOICES_REQUEST, {"session_id": "band-agent-room-2"})
        )
        await asyncio.sleep(0.05)
        operator.types("1")  # answers the prompt that owns the terminal
        assert (await asyncio.wait_for(first, timeout=5))["answer"] == "stable"
        await asyncio.sleep(0.05)  # queued prompt takes over the terminal
        operator.types("2")
        assert (await asyncio.wait_for(second, timeout=5))["answer"] == "beta"


class TestLogGate:
    def _root_with_capture(self) -> tuple[logging.Logger, list[str]]:
        written: list[str] = []

        class Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                written.append(record.getMessage())

        root = logging.getLogger()
        self._saved = root.handlers[:]
        root.handlers[:] = [Capture()]
        return root, written

    def _restore(self, root: logging.Logger) -> None:
        root.handlers[:] = self._saved

    def test_paused_records_are_deferred_not_lost(self):
        root, written = self._root_with_capture()
        try:
            console = OperatorConsole()
            console.gate_logging()
            gate = root.handlers[0]
            assert isinstance(gate, LogGate)

            gate.pause()
            logging.getLogger("band.test").warning("during prompt")
            assert written == []
            gate.resume()
            assert written == ["during prompt"]
            logging.getLogger("band.test").warning("after prompt")
            assert written == ["during prompt", "after prompt"]
        finally:
            self._restore(root)

    def test_second_console_adopts_existing_gates(self):
        """No double-wrap — and the second console still gates its prompts."""
        root, _ = self._root_with_capture()
        try:
            first = OperatorConsole()
            first.gate_logging()
            gated_once = root.handlers[:]

            second = OperatorConsole()
            second.gate_logging()
            assert root.handlers == gated_once
            assert not isinstance(gated_once[0]._target, LogGate)
            # The second console must pause the same gates, not none.
            assert second._gates == first._gates == gated_once
        finally:
            self._restore(root)

    @pytest.mark.asyncio
    async def test_first_ask_gates_root_logging_automatically(self, operator):
        """No setup ritual: the first question adopts the root handlers."""
        root, written = self._root_with_capture()
        try:
            console = make_console(operator)
            task = asyncio.create_task(console.ask(CHOICES_REQUEST, CONTEXT))
            await asyncio.sleep(0.05)
            assert all(isinstance(h, LogGate) for h in root.handlers)
            # Logs emitted while the prompt is open are parked, not shown.
            logging.getLogger("band.test").warning("mid prompt")
            assert written == []
            operator.types("2")
            answer = await asyncio.wait_for(task, timeout=5)
            assert answer == {"answer": "beta", "wasFreeform": False}
            assert written == ["mid prompt"]
        finally:
            self._restore(root)

    def test_shared_gates_stay_paused_until_every_prompt_resumes(self):
        """Pauses are counted: with two consoles sharing gates, the first
        resume must not un-park logs while the other prompt is open."""
        root, written = self._root_with_capture()
        try:
            console = OperatorConsole()
            console.gate_logging()
            gate = root.handlers[0]
            assert isinstance(gate, LogGate)

            gate.pause()  # console A's prompt opens
            gate.pause()  # console B's concurrent prompt opens
            logging.getLogger("band.test").warning("during B")
            gate.resume()  # A answers first
            assert written == []  # B's prompt is still open
            gate.resume()
            assert written == ["during B"]
        finally:
            self._restore(root)
