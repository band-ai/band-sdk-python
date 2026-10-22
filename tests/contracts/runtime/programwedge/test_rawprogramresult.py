from __future__ import annotations

import pytest

import tests.contracts.runtime.program as program_tests
from remote_agent.program import Program, ProgramError
from tests.contracts.runtime.program import ProgramFixture
from tests.contracts.runtime.programwedge.kit import (
    BAD_DELEGATE,
    BAD_HANDLED,
    VALID_DESCRIBE,
    raw_describe,
    raw_handle,
)


@pytest.mark.contract_isolation
class TestRawProgramResult(ProgramFixture):
    def test_host_rejects_worker_validation_bypasses(self) -> None:
        valid = {"type": "handled", "state": {}, "message": None, "snapshot": None}
        delegate = {
            "type": "delegate",
            "scope": "turn",
            "repair": False,
            "reason": "judgment",
        }
        variants = [
            BAD_HANDLED,
            BAD_DELEGATE,
            {},
            dict(valid, type="magic"),
            dict(valid, message={"kind": "", "payload": {}}),
            dict(valid, message={"kind": "answer", "payload": []}),
            dict(valid, snapshot={"count": 9}),
            dict(delegate, scope="conversation"),
            dict(delegate, repair=1),
            dict(delegate, reason=""),
            dict(delegate, reason="x" * 2001),
            {"schema": "1", "coverage": program_tests.SOURCE, "initial": {}},
        ]
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ProgramError):
                self.program(raw_handle(value)).handle({}, {}, {})

    def test_valid_raw_handled_and_delegate_still_work(self) -> None:
        for value in (
            {"type": "handled", "state": {}, "message": None, "snapshot": {}},
            {
                "type": "delegate",
                "scope": "ongoing",
                "repair": True,
                "reason": "judgment",
            },
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    self.program(raw_handle(value)).handle({}, {}, {}), value
                )

    def test_host_rejects_raw_describe_abi_violations(self) -> None:
        variants = [
            {},
            dict(VALID_DESCRIBE, unexpected=True),
            {key: value for key, value in VALID_DESCRIBE.items() if key != "initial"},
            *[dict(VALID_DESCRIBE, schema=value) for value in (None, 1, "", "x" * 101)],
            dict(VALID_DESCRIBE, initial=[]),
            dict(VALID_DESCRIBE, coverage=[]),
        ]
        for changes in (
            {"mode": "unknown"},
            {"handles": "ping"},
            {"handles": [1]},
            {"handles": [""]},
            {"delegates": []},
            {"delegates": "judgment"},
            {"delegates": [None]},
            {"delegates": [""]},
            {"unexpected": True},
        ):
            variants.append(
                dict(
                    VALID_DESCRIBE, coverage=dict(VALID_DESCRIBE["coverage"], **changes)
                )
            )
        variants.append(dict(VALID_DESCRIBE, coverage={"mode": "full", "handles": []}))
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ProgramError):
                self.program(raw_describe(value)).describe({})

    def test_valid_raw_describe_modes_survive_host_validation_and_replay(self) -> None:
        for mode in ("full", "partial", "llm_only"):
            coverage = dict(VALID_DESCRIBE["coverage"], mode=mode)
            value = dict(VALID_DESCRIBE, coverage=coverage)
            if mode == "full":
                coverage["delegates"] = []
            with self.subTest(mode=mode):
                program = self.program(raw_describe(value))
                self.assertEqual(program.describe({}), value)
                self.assertEqual(
                    Program.result(
                        program.execute(program.request("describe", view={})),
                        op="describe",
                    ),
                    value,
                )

    def test_host_rejects_extra_callback_reply_keys(self) -> None:
        variants = [
            {"type": "handled", "state": {}, "message": None, "unexpected": True},
            {
                "type": "delegate",
                "scope": "turn",
                "repair": False,
                "reason": "judgment",
                "unexpected": True,
            },
        ]
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ProgramError):
                self.program(raw_handle(value)).handle({}, {}, {})
