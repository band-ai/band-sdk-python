"""Each approval dialect reads the command its request gates."""

from __future__ import annotations

import pytest

from band.adapters.claude_sdk import (
    APPROVAL_REQUESTED_TEMPLATE as CLAUDE_REQUESTED,
)
from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.adapters.codex import APPROVAL_REQUESTED_TEMPLATE as CODEX_REQUESTED
from band.adapters.codex import CodexAdapter
from band.adapters.opencode.approvals.wording import APPROVAL_REQUESTED_TEMPLATE
from band.integrations.acp.cursor import PERMISSION_REQUESTED_TEMPLATE
from band.integrations.codex.types import CodexApprovalMethod
from tests.e2e.baseline.smoke.samples.approvals import DIALECTS
from tests.e2e.baseline.toolkit.adapters import Adapter
from tests.framework_conformance.approvalcommands.kit import agent_message

GATED_COMMAND = "cat approval.txt"


class TestDialectRequests:
    @pytest.mark.parametrize(
        ("adapter", "request_text"),
        [
            (
                Adapter.CLAUDE_SDK,
                CLAUDE_REQUESTED.format(
                    summary=ClaudeSDKAdapter._approval_summary(
                        "Bash", {"command": GATED_COMMAND}
                    ),
                    token="a-1",
                ),
            ),
            (
                Adapter.CODEX,
                CODEX_REQUESTED.format(
                    summary=CodexAdapter._approval_summary(
                        CodexApprovalMethod.COMMAND_EXECUTION,
                        {"command": GATED_COMMAND},
                    ),
                    token="1",
                ),
            ),
            (
                Adapter.CURSOR_ACP,
                PERMISSION_REQUESTED_TEMPLATE.format(
                    tool=GATED_COMMAND, token="p-1", options="allow-once, reject-once"
                ),
            ),
            (
                Adapter.OPENCODE,
                APPROVAL_REQUESTED_TEMPLATE.format(
                    permission="bash", patterns=GATED_COMMAND, request_id="per_1"
                ),
            ),
        ],
    )
    def test_each_dialect_reads_the_command_its_request_gates(
        self, adapter: Adapter, request_text: str
    ) -> None:
        dialect = DIALECTS[adapter]
        request = dialect.find_request([agent_message(request_text)])
        assert request is not None
        assert dialect.shell_command(request) == GATED_COMMAND
