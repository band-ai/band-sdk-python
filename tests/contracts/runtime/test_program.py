from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pytest

from remote_agent.program import ProgramError
from remote_agent.sdk import TurnContext
from tests.contracts.runtime.callbackdeadline import CALLBACK_LOOP, callback_deadline
from tests.contracts.runtime.program import SOURCE, ProgramFixture


class TestProgram(ProgramFixture):
    def test_sdk_snapshot_copies_json_state_and_does_not_write_a_separate_file(
        self,
    ) -> None:
        context = TurnContext({"definition": {"doc": "Respond precisely"}})
        state = {"count": 1}
        context.checkpoint(state)
        state["count"] = 2
        self.assertEqual(context.snapshot, {"count": 1})
        self.assertEqual(context.view["definition"]["doc"], "Respond precisely")

    @pytest.mark.contract_isolation
    def test_program_declares_partial_coverage_and_common_case_stages_a_snapshot(
        self,
    ) -> None:
        program = self.program()
        metadata = program.describe({})
        self.assertEqual(metadata["coverage"]["mode"], "partial")
        self.assertEqual(metadata["initial"], {"count": 0})
        result = program.handle(
            {"id": "q", "payload": {"text": "ping"}}, {"count": 0}, {}
        )
        self.assertEqual(
            result,
            {
                "type": "handled",
                "state": {"count": 1},
                "message": {
                    "kind": "answer",
                    "payload": {"request": "q", "text": "pong"},
                },
                "snapshot": {"count": 1},
            },
        )

    @pytest.mark.contract_isolation
    def test_unhandled_language_is_explicitly_delegated_for_one_turn(self) -> None:
        result = self.program().handle(
            {"payload": {"text": "What does this ambiguous request mean?"}},
            {"count": 0},
            {},
        )
        self.assertEqual(
            result,
            {
                "type": "delegate",
                "scope": "turn",
                "repair": False,
                "reason": "Requires language judgment",
            },
        )

    @pytest.mark.contract_linux
    def test_source_hash_detects_modified_code_before_it_executes(self) -> None:
        program = self.program()
        Path(program.artifact.path).write_text(
            SOURCE + "\nraise Exception('changed')\n"
        )
        with self.assertRaisesRegex(ProgramError, "hash"):
            program.describe({})

    @pytest.mark.contract_isolation
    def test_crash_timeout_and_excess_output_are_bounded_failures(self) -> None:
        for body, expected in [
            ("raise RuntimeError('boom')", "boom"),
            (CALLBACK_LOOP, "timeout"),
            ("print('x' * 2000000)", "output"),
        ]:
            with self.subTest(expected=expected):
                program = self.program(
                    SOURCE + "\ndef handle(event, state, context):\n    " + body + "\n",
                    timeout=10,
                )
                with (
                    callback_deadline()
                    if expected == "timeout"
                    else nullcontext() as deadline
                ):
                    with self.assertRaisesRegex(ProgramError, expected):
                        program.handle({}, {"count": 0}, {})
                    if deadline is not None:
                        self.assertTrue(deadline.entered and deadline.expired)

    @pytest.mark.contract_isolation
    def test_invalid_result_schema_and_stale_checkpoint_are_not_accepted(self) -> None:
        variants = [
            SOURCE.replace('STATE_VERSION = "1"', "STATE_VERSION = 1"),
            SOURCE.replace('"partial"', '"magic"'),
        ]
        for source in variants:
            with self.assertRaises(ProgramError):
                self.program(source).describe({})
        mismatch = SOURCE.replace(
            "context.checkpoint(state)", "context.checkpoint({'count': -1})"
        )
        with self.assertRaisesRegex(ProgramError, "snapshot"):
            self.program(mismatch).handle(
                {"id": "q", "payload": {"text": "ping"}}, {"count": 0}, {}
            )
