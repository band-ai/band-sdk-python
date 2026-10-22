from __future__ import annotations

import base64

import pytest

from remote_agent.commands import CommandError, call, result_from_capture
from tests.contracts.runtime.commandresults import CommandResultsFixture


class TestCommandResults(CommandResultsFixture):
    @pytest.mark.contract_linux
    def test_duplicate_fields_are_rejected_at_every_response_depth(self) -> None:
        for output in (
            '{"state":{},"state":{"count":99}}',
            '{"state":{"count":1,"count":99}}',
        ):
            with self.subTest(output=output), self.assertRaises(CommandError):
                call(self.command(output), {})

    @pytest.mark.contract_linux
    def test_deeply_nested_output_is_a_bounded_protocol_failure(self) -> None:
        output = '{"state":' + "[" * 1200 + "0" + "]" * 1200 + "}"
        with self.assertRaises(CommandError):
            call(self.command(output), {})

    def test_incomplete_or_unsuccessful_capture_cannot_be_used_as_a_result(
        self,
    ) -> None:
        base = {
            "profile": "remote-agent/process-result/1",
            "stdout": base64.b64encode(b"{}").decode(),
            "stderr": "",
            "failure": None,
            "complete": True,
            "exit_code": 0,
            "elapsed_ms": 1,
        }
        for changed in ({"complete": False}, {"exit_code": None}, {"exit_code": 1}):
            with self.subTest(changed=changed), self.assertRaises(CommandError):
                result_from_capture(dict(base, **changed))
