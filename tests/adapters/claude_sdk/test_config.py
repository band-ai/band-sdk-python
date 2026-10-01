"""A host's Claude config, loaded from YAML, governs a live room end to end;
a config that could never work is refused when it is loaded."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, get_args

import pytest
import yaml
from claude_agent_sdk.types import PermissionMode

from band.adapters.claude_sdk import (
    APPROVAL_REQUESTED_TEMPLATE,
    APPROVAL_TIMED_OUT_TEMPLATE,
    APPROVAL_UNAUTHORIZED_MESSAGE,
    ClaudePermissionMode,
    ClaudeSDKAdapterConfig,
)
from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.helpers import WRITE_NOTE, ClaudeRoom
from tests.baseline.decisions import ModelDecision

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]

ADMIN = {"id": "admin-1", "name": "Admin"}
LIST_FILES = ModelDecision.call("Bash", command="ls")

HOST_YAML = """
model: opus
fallback_model: sonnet
permission_mode: acceptEdits
setting_sources: [project]
cwd: {cwd}
turn_timeout_s: 600
cli:
  env: {{GITHUB_TOKEN: per-agent-token}}
  extra_args: {{debug-to-stderr: null}}
approvals:
  mode: manual
  wait_timeout_s: 60
  timeout_decision: accept
  authorized_senders: [admin-1]
"""


def host_config(tmp_path: Path, **changes: Any) -> ClaudeSDKAdapterConfig:
    """The host's YAML, with ``changes`` applied (a dict merges into a group)."""
    data = yaml.safe_load(HOST_YAML.format(cwd=tmp_path))
    for key, value in changes.items():
        data[key] = {**data[key], **value} if isinstance(value, dict) else value
    return ClaudeSDKAdapterConfig.model_validate(data)


def asked(token: str, summary: str) -> str:
    return APPROVAL_REQUESTED_TEMPLATE.format(summary=summary, token=token)


def timed_out(token: str, decision: str) -> str:
    return APPROVAL_TIMED_OUT_TEMPLATE.format(token=token, decision=decision)


@pytest.mark.looptime
async def test_a_yaml_host_config_governs_the_cli_and_the_approval_flow(
    claude_room: OpenRoom, tmp_path: Path
) -> None:
    """Every setting lands: the CLI starts with the host's model, mode,
    sources, directory, env and flags; a write acceptEdits would wave through
    still waits on the room; a non-admin cannot approve it; and when the admin
    stays silent the configured timeout decision (accept) lets it run."""
    room = await claude_room(host_config(tmp_path))
    room.claude.script([WRITE_NOTE, room.model_reply("Noted.")])

    await room.send("Jot a note")
    await room.send("/approve a-1")
    await room.settled()

    assert room.chat == [
        asked("a-1", "Write: notes.md"),
        APPROVAL_UNAUTHORIZED_MESSAGE,
        timed_out("a-1", "accept"),
        "Noted.",
    ]
    assert room.tool_outputs["Write"] == "Write ran"
    assert room.failures == []
    [session] = room.claude.sessions
    options = session.options
    assert (options.model, options.fallback_model) == ("opus", "sonnet")
    assert options.permission_mode == ClaudePermissionMode.ACCEPT_EDITS
    assert options.setting_sources == ["project"]
    assert Path(options.cwd) == tmp_path
    assert options.env == {"GITHUB_TOKEN": "per-agent-token"}
    assert options.extra_args == {"debug-to-stderr": None}


@pytest.mark.looptime
async def test_the_approval_wait_and_the_model_share_one_turn_limit(
    claude_room: OpenRoom, tmp_path: Path
) -> None:
    """The unanswered approval declines at 60s, inside the 120s turn limit;
    the model then stalls, so the turn limit interrupts it and reports a
    timeout. The interrupted turn is drained, so the same CLI process answers
    the next message with its own reply."""
    room = await claude_room(
        host_config(
            tmp_path, turn_timeout_s=120, approvals={"timeout_decision": "decline"}
        )
    )
    room.claude.script([LIST_FILES, Hold()], [room.model_reply("Back again.")])

    await room.send("List the files")
    await room.settled()
    await room.send("And now?", sender=ADMIN)

    assert room.chat == [
        asked("a-1", "Bash: `ls`"),
        timed_out("a-1", "decline"),
        "Back again.",
    ]
    assert room.tool_outputs["Bash"] == "Approval timed out, tool use declined"
    assert [failure["code"] for failure in room.reported_failures] == ["timeout"]
    assert [session.alive for session in room.claude.sessions] == [True]


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        pytest.param(
            {"turn_timeout_s": 60},
            "wait_timeout_s must be less than turn_timeout_s",
            id="approval-outlives-the-turn",
        ),
        pytest.param(
            {"permission_mode": "dontAsk"},
            "denies tool calls without consulting approvals",
            id="dont-ask-silences-the-approvals",
        ),
        pytest.param(
            {"permission_mode": "dontask"}, "permission_mode", id="misspelt-mode"
        ),
        pytest.param({"effort": "maximum"}, "effort", id="effort-the-sdk-lacks"),
        pytest.param(
            {"setting_sources": ["global"]}, "setting_sources", id="unknown-source"
        ),
        pytest.param({"cwd": "does-not-exist"}, "cwd", id="missing-directory"),
        pytest.param(
            {"approvals": {"mode": "ask"}}, "approvals.mode", id="unknown-approval-mode"
        ),
        pytest.param({"modle": "opus"}, "modle", id="misspelt-setting"),
    ],
)
def test_a_host_config_that_could_never_work_is_refused_on_load(
    tmp_path: Path, changes: dict[str, Any], error: str
) -> None:
    """Each change breaks one otherwise working host config, so the refusal
    is for that change alone."""
    with pytest.raises(ValueError, match=error):
        host_config(tmp_path, **changes)


def test_the_permission_enum_is_exactly_the_sdks_modes() -> None:
    """A mode the SDK adds or drops fails here, on the dependency bump."""
    assert set(ClaudePermissionMode) == set(get_args(PermissionMode))
