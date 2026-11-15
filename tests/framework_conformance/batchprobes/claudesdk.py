"""Batch-input probe for the Claude SDK adapter; see ``batchprobes``.

The input is what the real ``ClaudeSDKClient`` writes to the scripted Claude
CLI as the turn's prompt.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from band.core.types import AgentInput
from tests.baseline.decisions import ModelDecision
from tests.framework_conformance.batchprobes import BatchProbe, deliver


async def run_claude_sdk(inp: AgentInput, final_text: str) -> str:
    pytest.importorskip("claude_agent_sdk")
    from band.adapters.claude_sdk import (  # noqa: PLC0415 -- claude_sdk extra, absent from some lane venvs
        ClaudeSDKAdapter,
    )
    from tests.adapters.claude_sdk.fakecli import (  # noqa: PLC0415 -- claude_sdk extra, absent from some lane venvs
        FakeClaude,
    )

    claude = FakeClaude()
    claude.script([ModelDecision.text_reply(final_text)])
    adapter = ClaudeSDKAdapter()
    with patch(
        "band.integrations.claude_sdk.session_manager.create_transport",
        claude.transport,
    ):
        await adapter.on_started("Agent", "An agent under test")
        try:
            await deliver(adapter, inp)
        finally:
            await adapter.cleanup_all()
    claude.assert_done()
    return "\n".join(claude.prompts)


PROBES: dict[str, BatchProbe] = {"claude_sdk": BatchProbe(run=run_claude_sdk)}
