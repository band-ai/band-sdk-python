"""Tests for tool-call visibility via Block Kit plan/task UI.

When the brain emits ``tool_call`` / ``tool_result`` events (via
``Emit.EXECUTION``), the SlackAdapter renders them as a progressive
Block Kit message in the bound Slack thread. The plan is created on
the first ``tool_call`` and updated in place via ``chat.update`` as
work progresses.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.integrations.slack.adapter import _SlackTeeingTools
from band.integrations.slack.block_kit import (
    DEFAULT_WRITE_TOOL_NAMES,
    PlanState,
    PlanTask,
    TaskState,
    humanize_tool_name,
    plan_fallback_text,
    render_plan_blocks,
)
from band.integrations.slack.types import SlackRoomBinding
from band.runtime.tools import AgentTools


# ── humanize_tool_name ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("band_send_message", "Send message"),
        ("band_lookup_peers", "Lookup peers"),
        ("slack_send_message", "Send message"),
        ("my_custom_tool", "My custom tool"),
        ("snake_case_only", "Snake case only"),
        ("", ""),
        # When stripping the prefix produces an empty string, fall back
        # to the original name rather than presenting a blank label.
        ("band_", "band_"),
    ],
)
def test_humanize_tool_name(raw: str, expected: str):
    assert humanize_tool_name(raw) == expected


# ── render_plan_blocks ──────────────────────────────────────────────────────


def test_render_empty_plan_shows_working_header():
    blocks = render_plan_blocks(PlanState())
    assert len(blocks) == 1
    assert "Working on it" in blocks[0]["text"]["text"]
    assert blocks[0]["text"]["text"].startswith("*🤖")


def test_render_in_progress_task():
    plan = PlanState(
        tasks=[PlanTask(id="a", label="Lookup peers", state=TaskState.IN_PROGRESS)]
    )
    plan.tasks_by_id["a"] = plan.tasks[0]
    blocks = render_plan_blocks(plan)
    # Header + divider + 1 task = 3 blocks
    assert len(blocks) == 3
    assert blocks[0]["text"]["text"].startswith("*🤖")
    assert blocks[1]["type"] == "divider"
    assert "⏳" in blocks[2]["text"]["text"]
    assert "Lookup peers" in blocks[2]["text"]["text"]


def test_render_all_completed_flips_header_to_done():
    plan = PlanState(
        tasks=[
            PlanTask(id="a", label="Lookup peers", state=TaskState.COMPLETED),
            PlanTask(id="b", label="Read memories", state=TaskState.COMPLETED),
        ]
    )
    blocks = render_plan_blocks(plan)
    assert blocks[0]["text"]["text"] == "*✅ Done*"
    assert "✅" in blocks[2]["text"]["text"]
    assert "✅" in blocks[3]["text"]["text"]


def test_render_any_error_flips_header_to_done_with_errors():
    plan = PlanState(
        tasks=[
            PlanTask(id="a", label="Lookup peers", state=TaskState.COMPLETED),
            PlanTask(
                id="b",
                label="Add participant",
                state=TaskState.ERROR,
                error_message="peer not found",
            ),
        ]
    )
    blocks = render_plan_blocks(plan)
    assert blocks[0]["text"]["text"] == "*⚠️ Done with errors*"
    err_block_text = blocks[-1]["text"]["text"]
    assert "❌" in err_block_text
    assert "peer not found" in err_block_text


def test_write_tool_emphasis():
    plan = PlanState(
        tasks=[
            PlanTask(
                id="a",
                label="Add participant",
                state=TaskState.IN_PROGRESS,
                is_write=True,
            ),
            PlanTask(
                id="b",
                label="Lookup peers",
                state=TaskState.IN_PROGRESS,
                is_write=False,
            ),
        ]
    )
    blocks = render_plan_blocks(plan)
    write_block = blocks[2]["text"]["text"]
    read_block = blocks[3]["text"]["text"]
    # Write tool: bold + pencil emoji prefix.
    assert "✏️" in write_block
    assert "*Add participant*" in write_block
    # Read tool: no bold, no pencil.
    assert "✏️" not in read_block
    assert "*Lookup peers*" not in read_block


def test_render_truncates_long_error_messages():
    long_err = "x" * 500
    plan = PlanState(
        tasks=[
            PlanTask(
                id="a",
                label="Lookup peers",
                state=TaskState.ERROR,
                error_message=long_err,
            )
        ]
    )
    blocks = render_plan_blocks(plan)
    text = blocks[-1]["text"]["text"]
    # Truncated with ellipsis; total error portion ≤ 240 chars.
    assert "…" in text
    assert len([c for c in text if c == "x"]) <= 240


def test_plan_fallback_text_states():
    assert plan_fallback_text(PlanState()) == "Working on it…"
    plan_in_progress = PlanState(
        tasks=[PlanTask(id="a", label="x", state=TaskState.IN_PROGRESS)]
    )
    assert plan_fallback_text(plan_in_progress) == "Working on it…"
    plan_done = PlanState(
        tasks=[
            PlanTask(id="a", label="x", state=TaskState.COMPLETED),
            PlanTask(id="b", label="y", state=TaskState.ERROR),
        ]
    )
    assert plan_fallback_text(plan_done) == "Done"


def test_render_caps_blocks_at_slack_limit():
    """Slack rejects >50 blocks; large plans are capped with an overflow note.

    49 tasks would otherwise render header + divider + 49 = 51 blocks. We
    cap at 50 by truncating tasks and appending a single "…and N more"
    summary, so the message is still postable.
    """
    plan = PlanState(
        tasks=[
            PlanTask(id=str(i), label=f"task {i}", state=TaskState.COMPLETED)
            for i in range(49)
        ]
    )
    blocks = render_plan_blocks(plan)

    assert len(blocks) == 50
    # Last block is the overflow summary. header(1) + divider(1) + 47 task
    # sections + 1 overflow = 50, so 49 - 47 = 2 tasks are summarised.
    assert blocks[-1]["text"]["text"] == "_…and 2 more_"


def test_render_does_not_cap_at_or_below_limit():
    """A plan that fits in 50 blocks renders every task, no overflow note."""
    # header + divider + 48 task sections == 50 blocks exactly.
    plan = PlanState(
        tasks=[
            PlanTask(id=str(i), label=f"task {i}", state=TaskState.COMPLETED)
            for i in range(48)
        ]
    )
    blocks = render_plan_blocks(plan)

    assert len(blocks) == 50
    assert all("…and" not in str(b) for b in blocks)


def test_default_write_tool_names_includes_known_mutators():
    assert "band_send_message" in DEFAULT_WRITE_TOOL_NAMES
    assert "band_add_participant" in DEFAULT_WRITE_TOOL_NAMES
    assert "band_store_memory" in DEFAULT_WRITE_TOOL_NAMES
    # Read-only tools should NOT be in there.
    assert "band_lookup_peers" not in DEFAULT_WRITE_TOOL_NAMES
    assert "band_list_memories" not in DEFAULT_WRITE_TOOL_NAMES


# ── _SlackTeeingTools tool-execution hook ───────────────────────────────────
#
# The plan-rendering hook now lives in ``execute_tool_call`` (not
# ``send_event``) so Slack progress is independent of the brain's
# ``Emit.EXECUTION`` setting. Tests patch the parent class's
# ``execute_tool_call_structured`` to return controlled outcomes — the
# Slack hook derives task success/failure from the structured ``ok``
# flag, not from the returned string.


def _make_tools(
    write_tool_names: frozenset[str] | set[str] | None = None,
    show_tool_progress: bool = True,
) -> tuple[_SlackTeeingTools, MagicMock, AsyncMock]:
    rest = MagicMock()
    base = AgentTools(room_id="r1", rest=rest, participants=[])
    slack = AsyncMock()
    slack.chat_postMessage = AsyncMock(return_value={"ok": True, "ts": "msg-1.000"})
    slack.chat_update = AsyncMock(return_value={"ok": True, "ts": "msg-1.000"})
    tools = _SlackTeeingTools(
        wrap=base,
        slack=slack,
        binding=SlackRoomBinding(app_slug="dev", channel="C", thread_ts="1.0"),
        write_tool_names=write_tool_names,
        show_tool_progress=show_tool_progress,
    )
    return tools, rest, slack


def _patch_super_execute(
    return_value: object = "ok",
    *,
    ok: bool = True,
    error_message: str | None = None,
) -> tuple[Any, AsyncMock]:
    """Patch ``AgentTools.execute_tool_call_structured`` so super calls return
    controlled :class:`ToolCallOutcome` results."""
    from band.runtime.tools import ToolCallOutcome

    mock = AsyncMock(
        return_value=ToolCallOutcome(
            value=return_value, ok=ok, error_message=error_message
        )
    )
    return patch.object(AgentTools, "execute_tool_call_structured", mock), mock


@pytest.mark.asyncio
async def test_first_tool_call_posts_plan_message():
    tools, _, slack = _make_tools()
    ctx, super_mock = _patch_super_execute(return_value=[])
    with ctx:
        await tools.execute_tool_call("band_lookup_peers", {})

    slack.chat_postMessage.assert_awaited_once()
    kwargs = slack.chat_postMessage.await_args.kwargs
    assert kwargs["channel"] == "C"
    assert kwargs["thread_ts"] == "1.0"
    blocks = kwargs["blocks"]
    assert any("Lookup peers" in (b.get("text", {}).get("text", "")) for b in blocks)
    # Plan message_ts captured for subsequent updates.
    assert tools._plan.message_ts == "msg-1.000"
    # Super was called (the real tool executed).
    super_mock.assert_awaited_once_with("band_lookup_peers", {})


@pytest.mark.asyncio
async def test_second_tool_call_updates_existing_plan_message():
    tools, _, slack = _make_tools()
    ctx, _ = _patch_super_execute(return_value="ok")
    with ctx:
        await tools.execute_tool_call("band_lookup_peers", {})
        await tools.execute_tool_call("band_add_participant", {})

    # First call: 1 post (in_progress) + 1 update (completed)
    # Second call: 2 more updates (add task in_progress, mark completed)
    # = 1 post total, ≥3 updates
    slack.chat_postMessage.assert_awaited_once()
    assert slack.chat_update.await_count >= 3
    update_kwargs = slack.chat_update.await_args.kwargs
    assert update_kwargs["channel"] == "C"
    assert update_kwargs["ts"] == "msg-1.000"
    final_blocks = update_kwargs["blocks"]
    tasks_text = " ".join(
        b.get("text", {}).get("text", "")
        for b in final_blocks
        if b["type"] == "section"
    )
    assert "Lookup peers" in tasks_text
    assert "Add participant" in tasks_text


@pytest.mark.asyncio
async def test_tool_completes_flips_task_to_completed():
    tools, _, _ = _make_tools()
    ctx, _ = _patch_super_execute(return_value="ok")
    with ctx:
        await tools.execute_tool_call("band_lookup_peers", {})
    # After completion, exactly one task in COMPLETED state.
    assert len(tools._plan.tasks) == 1
    assert tools._plan.tasks[0].state == TaskState.COMPLETED


@pytest.mark.asyncio
async def test_failed_tool_call_flips_task_to_error():
    """A *real* base-tool failure must mark the task ERROR.

    Regression for the string-heuristic bug: ``AgentTools`` failures have
    no single ``Error:`` prefix (they surface as "Invalid arguments for
    …", "Error executing …", "Unknown tool: …"), so the plan UI must
    derive failure from the structured ``ok`` flag. Here we drive a real
    validation failure (missing required args) — no REST call, no mock —
    so the test exercises the actual error string the base tools produce.
    """
    tools, _, slack = _make_tools()

    result = await tools.execute_tool_call("band_add_participant", {})

    # Real base-class failure string — explicitly NOT "Error:"-prefixed.
    assert isinstance(result, str)
    assert result.startswith("Invalid arguments for band_add_participant")
    task = tools._plan.tasks[0]
    assert task.state == TaskState.ERROR
    assert task.error_message and "Invalid arguments" in task.error_message
    update_blocks = slack.chat_update.await_args.kwargs["blocks"]
    assert update_blocks[0]["text"]["text"] == "*⚠️ Done with errors*"


@pytest.mark.asyncio
async def test_super_exception_marks_task_error_and_reraises():
    tools, _, _ = _make_tools()
    err = RuntimeError("boom")
    from band.runtime.tools import AgentTools as _AT

    # BandToolError (and anything else that propagates out of the
    # structured call) must mark the task ERROR and re-raise.
    with patch.object(_AT, "execute_tool_call_structured", AsyncMock(side_effect=err)):
        with pytest.raises(RuntimeError, match="boom"):
            await tools.execute_tool_call("band_lookup_peers", {})

    task = tools._plan.tasks[0]
    assert task.state == TaskState.ERROR
    assert "boom" in task.error_message


@pytest.mark.asyncio
async def test_slack_send_message_does_not_appear_as_plan_task():
    """The Slack-only reply tool IS the answer, not progress."""
    tools, _, slack = _make_tools()

    await tools.execute_tool_call(
        "slack_send_message", {"content": "Here is the answer."}
    )

    # The plan stays empty; no plan message posted; just the user-facing reply.
    assert tools._plan.tasks == []
    # chat.postMessage WAS called — but it was the slack_send_message tool
    # posting the reply, not a plan-block placeholder.
    slack.chat_postMessage.assert_awaited_once()
    assert slack.chat_postMessage.await_args.kwargs["text"] == "Here is the answer."
    assert "blocks" not in slack.chat_postMessage.await_args.kwargs


@pytest.mark.asyncio
async def test_show_tool_progress_false_disables_plan_blocks():
    """Toggle independently of brain's emit setting."""
    tools, _, slack = _make_tools(show_tool_progress=False)
    ctx, super_mock = _patch_super_execute(return_value="ok")
    with ctx:
        await tools.execute_tool_call("band_lookup_peers", {})
        await tools.execute_tool_call("band_add_participant", {})

    # Plan stayed empty; no Slack writes; tools still executed.
    assert tools._plan.tasks == []
    slack.chat_postMessage.assert_not_awaited()
    slack.chat_update.assert_not_awaited()
    assert super_mock.await_count == 2


@pytest.mark.asyncio
async def test_write_tool_emphasis_propagates_from_adapter_config():
    """Custom write_tool_names override the default set."""
    tools, _, slack = _make_tools(write_tool_names={"band_lookup_peers"})
    ctx, _ = _patch_super_execute(return_value="ok")
    with ctx:
        await tools.execute_tool_call("band_lookup_peers", {})

    blocks = slack.chat_postMessage.await_args.kwargs["blocks"]
    task_block_text = blocks[-1]["text"]["text"]
    # Lookup peers is now treated as a write tool (per the override).
    assert "✏️" in task_block_text
    assert "*Lookup peers*" in task_block_text


@pytest.mark.asyncio
async def test_slack_failure_does_not_break_tool_execution():
    tools, _, slack = _make_tools()
    slack.chat_postMessage = AsyncMock(side_effect=RuntimeError("slack down"))
    ctx, super_mock = _patch_super_execute(return_value="ok")
    with ctx:
        result = await tools.execute_tool_call("band_lookup_peers", {})

    # The underlying tool still executed and its result was returned.
    assert result == "ok"
    super_mock.assert_awaited_once()
    # message_ts was never captured (post failed) so subsequent calls
    # would try to post again rather than update a missing ts.
    assert tools._plan.message_ts is None


@pytest.mark.asyncio
async def test_full_sequence_two_tools():
    tools, _, slack = _make_tools()
    ctx, _ = _patch_super_execute(return_value="ok")
    with ctx:
        await tools.execute_tool_call("band_lookup_peers", {})
        await tools.execute_tool_call("band_add_participant", {})

    slack.chat_postMessage.assert_awaited_once()
    # Final plan state: both completed.
    assert len(tools._plan.tasks) == 2
    assert all(t.state == TaskState.COMPLETED for t in tools._plan.tasks)
    # Final update shows "Done" header.
    final_blocks = slack.chat_update.await_args_list[-1].kwargs["blocks"]
    assert final_blocks[0]["text"]["text"] == "*✅ Done*"
