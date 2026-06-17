"""Gated live L4 cold-restart scenarios.

Run with:
    E2E_TESTS_ENABLED=true E2E_BASELINE_L4_LIVE=true LANGGRAPH_RESTART_SMOKE=true \
        uv run pytest tests/e2e/scenarios/test_baseline_l4_rehydration.py -v -s --no-cov
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import queue
import time
from pathlib import Path
from typing import Any

import pytest
from thenvoi_rest import AsyncRestClient
from thenvoi_rest.types import ParticipantRequest

from thenvoi.agent import Agent
from thenvoi.core.simple_adapter import ProviderUsageSnapshot

from tests.e2e.adapters.conftest import (
    AdapterFactory,
    BASELINE_DEFAULT_PROVIDER_USAGE_ADAPTER_FACTORIES,
    BASELINE_DEFAULT_PROVIDER_USAGE_BLOCKED_ADAPTER_NAMES,
)
from tests.e2e.baseline_artifacts import (
    aggregate_provider_usage,
    baseline_pricing_from_env,
    start_baseline_tier2_timer,
    write_baseline_tier2_artifact,
    write_baseline_tier2_blocked_artifact,
    write_provider_usage_blocked_artifact_if_needed,
)
from tests.e2e.conftest import E2EAgentCredentials, E2ESettings, requires_e2e
from tests.e2e.helpers import (
    assert_content_contains,
    fetch_chat_messages,
    mention_ids,
    message_ids,
    message_value,
    participant_ids,
    send_trigger_message,
    wait_full_window_for_new_agent_text_messages,
    wait_until_participant_absent,
    wait_until_participant_present,
)

_STEP_TIMEOUT = 90.0
_L4_SCENARIO_REFS = [
    "L4.request.cold_start_history",
    "L4.request.offline_pending_once",
    "L4.request.handled_message_dedup",
    "L4.request.completed_tool_no_requeue",
]


def _l4_live_blocked_reason() -> str | None:
    if os.environ.get("E2E_BASELINE_L4_LIVE") != "true":
        return "tier2_blocked: E2E_BASELINE_L4_LIVE=true not set for live L4 flow"
    missing = [
        name
        for name in ("E2E_ECHO_AGENT_ID", "E2E_ECHO_AGENT_NAME")
        if not os.environ.get(name)
    ]
    if missing:
        return f"tier2_blocked: missing live L4 Echo configuration {', '.join(missing)}"
    return None


def _l4_langgraph_live_blocked_reason() -> str | None:
    blocked_reason = _l4_live_blocked_reason()
    if blocked_reason:
        return blocked_reason
    if os.environ.get("LANGGRAPH_RESTART_SMOKE") != "true":
        return "tier2_blocked: LANGGRAPH_RESTART_SMOKE=true not set for supported LangGraph cold-restart proof"
    return None


def _marker_count(messages: list[object], marker: str) -> int:
    return sum(
        1
        for message in messages
        if marker in str(message_value(message, "content") or "")
    )


def _serialize_provider_usage_snapshots(
    snapshots: list[ProviderUsageSnapshot],
) -> list[dict[str, Any]]:
    return [
        {
            "source": snapshot.source,
            "input_tokens": snapshot.input_tokens,
            "output_tokens": snapshot.output_tokens,
            "total_tokens": snapshot.total_tokens,
            "api_call_count": snapshot.api_call_count,
            "cost_usd": snapshot.cost_usd,
            "raw": snapshot.raw,
        }
        for snapshot in snapshots
    ]


def _deserialize_provider_usage_snapshots(
    snapshots: list[dict[str, Any]],
) -> list[ProviderUsageSnapshot]:
    return [
        ProviderUsageSnapshot(
            source=str(snapshot["source"]),
            input_tokens=int(snapshot["input_tokens"]),
            output_tokens=int(snapshot["output_tokens"]),
            total_tokens=int(snapshot["total_tokens"]),
            api_call_count=int(snapshot.get("api_call_count", 1)),
            cost_usd=snapshot.get("cost_usd"),
            raw=dict(snapshot.get("raw") or {}),
        )
        for snapshot in snapshots
    ]


def _write_l4_usage_snapshots(
    usage_path: Path,
    snapshots: list[ProviderUsageSnapshot],
) -> None:
    usage_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = usage_path.with_name(f"{usage_path.name}.tmp")
    tmp_path.write_text(
        json.dumps(_serialize_provider_usage_snapshots(snapshots), allow_nan=False)
        + "\n"
    )
    tmp_path.replace(usage_path)


def _read_l4_usage_snapshots(usage_path: Path) -> list[ProviderUsageSnapshot]:
    if not usage_path.exists():
        return []
    raw = json.loads(usage_path.read_text())
    if not isinstance(raw, list):
        return []
    return _deserialize_provider_usage_snapshots(raw)


def _usage_api_call_count(snapshots: list[ProviderUsageSnapshot]) -> int:
    return sum(snapshot.api_call_count for snapshot in snapshots)


def _wait_for_l4_usage_snapshots(
    usage_path: Path,
    *,
    min_api_calls: int,
    timeout: float,
) -> list[ProviderUsageSnapshot]:
    deadline = time.monotonic() + timeout
    last_snapshots: list[ProviderUsageSnapshot] = []
    while time.monotonic() < deadline:
        last_snapshots = _read_l4_usage_snapshots(usage_path)
        if _usage_api_call_count(last_snapshots) >= min_api_calls:
            return last_snapshots
        time.sleep(0.2)
    raise AssertionError(
        "tier2_blocked: first agent process did not persist provider usage before kill"
    )


async def _run_l4_first_agent_child_async(
    *,
    adapter_name: str,
    agent_id: str,
    api_key: str,
    ws_url: str,
    rest_url: str,
    usage_path: Path,
    ready_queue: multiprocessing.Queue,
) -> None:
    settings = E2ESettings()
    adapter = BASELINE_DEFAULT_PROVIDER_USAGE_ADAPTER_FACTORIES[adapter_name](settings)
    adapter.clear_provider_usage()
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    async def write_usage_until_killed() -> None:
        while True:
            _write_l4_usage_snapshots(usage_path, adapter.provider_usage_snapshots())
            await asyncio.sleep(0.25)

    async with agent:
        usage_task = asyncio.create_task(write_usage_until_killed())
        ready_queue.put({"status": "ready"})
        try:
            await asyncio.Event().wait()
        finally:
            usage_task.cancel()
            _write_l4_usage_snapshots(usage_path, adapter.provider_usage_snapshots())


def _run_l4_first_agent_child(
    adapter_name: str,
    agent_id: str,
    api_key: str,
    ws_url: str,
    rest_url: str,
    usage_path: str,
    ready_queue: multiprocessing.Queue,
) -> None:
    try:
        asyncio.run(
            _run_l4_first_agent_child_async(
                adapter_name=adapter_name,
                agent_id=agent_id,
                api_key=api_key,
                ws_url=ws_url,
                rest_url=rest_url,
                usage_path=Path(usage_path),
                ready_queue=ready_queue,
            )
        )
    except BaseException as exc:
        ready_queue.put({"status": "error", "message": repr(exc)})
        raise


def _wait_for_l4_child_ready(
    process: multiprocessing.Process,
    ready_queue: multiprocessing.Queue,
    *,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process.is_alive():
            raise AssertionError(
                f"tier2_blocked: first agent process exited before readiness: {process.exitcode}"
            )
        try:
            message = ready_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        if message.get("status") == "ready":
            return
        raise AssertionError(
            "tier2_blocked: first agent process failed before readiness: "
            f"{message.get('message')}"
        )
    raise AssertionError("tier2_blocked: first agent process did not become ready")


def _terminate_l4_child_process(process: multiprocessing.Process) -> None:
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)
    if process.is_alive():
        raise AssertionError("tier2_blocked: first agent process survived termination")


_L4_LIVE_BLOCKED_REASON = _l4_live_blocked_reason()
pytestmark = pytest.mark.skipif(
    _L4_LIVE_BLOCKED_REASON is not None,
    reason=_L4_LIVE_BLOCKED_REASON or "tier2_blocked: unknown L4 live block",
)


@pytest.fixture(
    params=tuple(BASELINE_DEFAULT_PROVIDER_USAGE_ADAPTER_FACTORIES.items()),
    ids=lambda item: item[0],
)
def adapter_entry(request: pytest.FixtureRequest) -> tuple[str, AdapterFactory]:
    adapter_name, factory = request.param
    return str(adapter_name), factory


@pytest.mark.parametrize(
    "adapter_name", BASELINE_DEFAULT_PROVIDER_USAGE_BLOCKED_ADAPTER_NAMES
)
def test_l4_live_unsupported_adapter_rows_write_blocked_artifacts_when_configured(
    adapter_name: str,
) -> None:
    blocked_reason = _l4_live_blocked_reason()
    if blocked_reason:
        pytest.skip(blocked_reason)
    blocked_reason = write_provider_usage_blocked_artifact_if_needed(
        scenario_id="L4.request.cold_start_history",
        scenario_refs=_L4_SCENARIO_REFS,
        adapter=adapter_name,
    )
    assert blocked_reason is not None


@pytest.mark.asyncio
@pytest.mark.timeout(700)
@requires_e2e
async def test_l4_live_adapter_cold_restart_rehydrates_without_replaying_invite_when_configured(
    e2e_config: E2ESettings,
    adapter_entry: tuple[str, AdapterFactory],
    api_client: AsyncRestClient,
    e2e_adapter_room: tuple[str, str, str],
    e2e_adapter_agent_credentials: E2EAgentCredentials,
    tmp_path: Path,
) -> None:
    blocked_reason = _l4_live_blocked_reason()
    if blocked_reason:
        pytest.skip(blocked_reason)

    adapter_name, factory = adapter_entry
    factory(e2e_config)
    timer = start_baseline_tier2_timer()
    pricing = baseline_pricing_from_env()
    input_texts: list[str] = []
    output_texts: list[str] = []
    chat_id, _user_id, _user_name = e2e_adapter_room
    agent_id = e2e_adapter_agent_credentials.agent_id
    agent_name = e2e_adapter_agent_credentials.name
    echo_id = os.environ["E2E_ECHO_AGENT_ID"]

    try:
        await api_client.human_api_participants.add_my_chat_participant(
            chat_id,
            participant=ParticipantRequest(participant_id=agent_id, role="member"),
        )
    except Exception as exc:
        if getattr(exc, "status_code", None) != 409:
            raise

    if echo_id in await participant_ids(api_client, chat_id):
        await api_client.human_api_participants.remove_my_chat_participant(
            chat_id,
            echo_id,
        )
        await wait_until_participant_absent(api_client, chat_id, echo_id, _STEP_TIMEOUT)

    usage_path = tmp_path / f"{adapter_name}-first-agent-usage.json"
    spawn_context = multiprocessing.get_context("spawn")
    ready_queue = spawn_context.Queue()
    first_agent_process = spawn_context.Process(
        target=_run_l4_first_agent_child,
        args=(
            adapter_name,
            agent_id,
            e2e_adapter_agent_credentials.api_key,
            e2e_config.thenvoi_ws_url,
            e2e_config.thenvoi_base_url,
            str(usage_path),
            ready_queue,
        ),
    )
    first_agent_process.start()
    first_agent_exitcode: int | None = None
    try:
        _wait_for_l4_child_ready(
            first_agent_process,
            ready_queue,
            timeout=_STEP_TIMEOUT,
        )
        before_step_1 = message_ids(await fetch_chat_messages(api_client, chat_id))
        step_1_prompt = (
            "for later: the three keywords are ELEPHANT SAXOPHONE MIDNIGHT "
            "and my favorite color is TURQUOISE — then invite Echo to this room"
        )
        input_texts.append(step_1_prompt)
        step_1_message_id = await send_trigger_message(
            api_client,
            chat_id,
            step_1_prompt,
            agent_name,
            agent_id,
        )
        step_1_replies = await wait_full_window_for_new_agent_text_messages(
            api_client,
            chat_id,
            agent_id,
            before_step_1,
            timeout=_STEP_TIMEOUT,
        )
        assert len(step_1_replies) == 1, [
            message_value(message, "content") for message in step_1_replies
        ]
        output_texts.extend(
            str(message_value(message, "content") or "") for message in step_1_replies
        )
        await wait_until_participant_present(
            api_client, chat_id, echo_id, _STEP_TIMEOUT
        )

        before_marker = message_ids(await fetch_chat_messages(api_client, chat_id))
        marker_prompt = "repeat the word UNIQUE_MARKER_789"
        input_texts.append(marker_prompt)
        marker_message_id = await send_trigger_message(
            api_client,
            chat_id,
            marker_prompt,
            agent_name,
            agent_id,
        )
        marker_replies = await wait_full_window_for_new_agent_text_messages(
            api_client,
            chat_id,
            agent_id,
            before_marker,
            timeout=_STEP_TIMEOUT,
        )
        assert len(marker_replies) == 1, [
            message_value(message, "content") for message in marker_replies
        ]
        output_texts.extend(
            str(message_value(message, "content") or "") for message in marker_replies
        )
        assert_content_contains(marker_replies, "UNIQUE_MARKER_789")
        try:
            pre_restart_snapshots = _wait_for_l4_usage_snapshots(
                usage_path,
                min_api_calls=2,
                timeout=10,
            )
        except AssertionError as exc:
            write_baseline_tier2_blocked_artifact(
                scenario_id="L4.request.cold_start_history",
                scenario_refs=_L4_SCENARIO_REFS,
                adapter=adapter_name,
                reason=str(exc),
            )
            raise
    finally:
        _terminate_l4_child_process(first_agent_process)
        first_agent_exitcode = first_agent_process.exitcode
    assert not first_agent_process.is_alive()
    assert first_agent_exitcode is not None and first_agent_exitcode != 0

    await api_client.human_api_participants.remove_my_chat_participant(chat_id, echo_id)
    await wait_until_participant_absent(api_client, chat_id, echo_id, _STEP_TIMEOUT)

    restart_prompt = (
        "what were the three keywords and favorite color I gave you earlier?"
    )
    input_texts.append(restart_prompt)
    before_restart = message_ids(await fetch_chat_messages(api_client, chat_id))
    restart_message_id = await send_trigger_message(
        api_client,
        chat_id,
        restart_prompt,
        agent_name,
        agent_id,
    )

    restarted_adapter = factory(e2e_config)
    restarted_adapter.clear_provider_usage()
    restarted_agent = Agent.create(
        adapter=restarted_adapter,
        agent_id=agent_id,
        api_key=e2e_adapter_agent_credentials.api_key,
        ws_url=e2e_config.thenvoi_ws_url,
        rest_url=e2e_config.thenvoi_base_url,
    )
    async with restarted_agent:
        restart_replies = await wait_full_window_for_new_agent_text_messages(
            api_client,
            chat_id,
            agent_id,
            before_restart,
            timeout=_STEP_TIMEOUT,
        )
        assert len(restart_replies) == 1, [
            message_value(message, "content") for message in restart_replies
        ]
        output_texts.extend(
            str(message_value(message, "content") or "") for message in restart_replies
        )
        recalled_terms = ["ELEPHANT", "SAXOPHONE", "MIDNIGHT", "TURQUOISE"]
        for term in recalled_terms:
            assert_content_contains(restart_replies, term)
        echo_absent_after_restart = echo_id not in await participant_ids(
            api_client, chat_id
        )
        assert echo_absent_after_restart
        all_messages_after_restart = await fetch_chat_messages(api_client, chat_id)
        all_agent_messages_after_restart = [
            message
            for message in all_messages_after_restart
            if message_value(message, "sender_id") == agent_id
        ]
        post_restart_agent_messages = [
            message
            for message in all_agent_messages_after_restart
            if str(message_value(message, "id")) not in before_restart
        ]
        total_marker_count_after_restart = _marker_count(
            all_agent_messages_after_restart, "UNIQUE_MARKER_789"
        )
        post_restart_marker_count = _marker_count(
            post_restart_agent_messages, "UNIQUE_MARKER_789"
        )
        post_restart_echo_mentions = sum(
            1
            for message in post_restart_agent_messages
            if echo_id in mention_ids(message)
        )
        assert total_marker_count_after_restart == 1
        assert post_restart_marker_count == 0
        assert post_restart_echo_mentions == 0
        first_post_restart_usage = aggregate_provider_usage(
            restarted_adapter.provider_usage_snapshots()
        )
        history_to_new_token_ratio = (
            first_post_restart_usage.input_tokens
            / first_post_restart_usage.output_tokens
            if first_post_restart_usage.output_tokens > 0
            else 0
        )

        before_are_you_there = message_ids(
            await fetch_chat_messages(api_client, chat_id)
        )
        liveness_prompt = "are you there?"
        input_texts.append(liveness_prompt)
        liveness_message_id = await send_trigger_message(
            api_client,
            chat_id,
            liveness_prompt,
            agent_name,
            agent_id,
        )
        final_replies = await wait_full_window_for_new_agent_text_messages(
            api_client,
            chat_id,
            agent_id,
            before_are_you_there,
            timeout=_STEP_TIMEOUT,
        )
        assert len(final_replies) == 1, [
            message_value(message, "content") for message in final_replies
        ]
        output_texts.extend(
            str(message_value(message, "content") or "") for message in final_replies
        )
        echo_absent_after_liveness = echo_id not in await participant_ids(
            api_client, chat_id
        )
        assert echo_absent_after_liveness
        pre_restart_usage = aggregate_provider_usage(pre_restart_snapshots)
        post_restart_usage = aggregate_provider_usage(
            restarted_adapter.provider_usage_snapshots()
        )
        write_baseline_tier2_artifact(
            scenario_id="L4.request.cold_start_history",
            scenario_refs=_L4_SCENARIO_REFS,
            adapter=adapter_name,
            timer=timer,
            pricing=pricing,
            provider_usage=aggregate_provider_usage(
                [
                    *pre_restart_snapshots,
                    *restarted_adapter.provider_usage_snapshots(),
                ]
            ),
            input_texts=input_texts,
            output_texts=output_texts,
            observed_agent_text_message_count=(
                len(step_1_replies)
                + len(marker_replies)
                + len(restart_replies)
                + len(final_replies)
            ),
            evidence={
                "L4.request.cold_start_history": {
                    "observation_window_seconds": _STEP_TIMEOUT,
                    "restart_prompt_message_id": restart_message_id,
                    "restart_reply_count": len(restart_replies),
                    "restart_reply_id": str(message_value(restart_replies[0], "id")),
                    "recalled_terms": recalled_terms,
                    "first_agent_restart_boundary": "terminated_child_process",
                    "first_agent_process_exitcode": first_agent_exitcode,
                    "pre_restart_provider_usage_api_calls": _usage_api_call_count(
                        pre_restart_snapshots
                    ),
                },
                "L4.request.offline_pending_once": {
                    "offline_prompt_message_id": restart_message_id,
                    "restart_reply_count": len(restart_replies),
                    "restart_reply_id": str(message_value(restart_replies[0], "id")),
                    "observation_window_seconds": _STEP_TIMEOUT,
                },
                "L4.request.handled_message_dedup": {
                    "marker_prompt_message_id": marker_message_id,
                    "marker_reply_id": str(message_value(marker_replies[0], "id")),
                    "agent_marker_count_after_restart": total_marker_count_after_restart,
                    "post_restart_marker_reply_count": post_restart_marker_count,
                    "post_restart_agent_message_count": len(
                        post_restart_agent_messages
                    ),
                },
                "L4.request.completed_tool_no_requeue": {
                    "invite_prompt_message_id": step_1_message_id,
                    "echo_absent_after_restart": echo_absent_after_restart,
                    "echo_absent_after_liveness": echo_absent_after_liveness,
                    "post_restart_echo_mentions": post_restart_echo_mentions,
                    "liveness_prompt_message_id": liveness_message_id,
                    "liveness_reply_count": len(final_replies),
                    "liveness_reply_id": str(message_value(final_replies[0], "id")),
                },
            },
            platform_observations=[
                {
                    "kind": "message",
                    "id": str(message_value(restart_replies[0], "id")),
                    "assertion": "exactly one post-restart hydrated recall reply",
                    "scenario_refs": [
                        "L4.request.cold_start_history",
                        "L4.request.offline_pending_once",
                    ],
                },
                {
                    "kind": "message_count",
                    "id": chat_id,
                    "assertion": "UNIQUE_MARKER_789 appears once overall and zero times after restart",
                    "scenario_ref": "L4.request.handled_message_dedup",
                },
                {
                    "kind": "room_state",
                    "id": chat_id,
                    "assertion": "Echo absent after restart and no post-restart Echo mention",
                    "scenario_ref": "L4.request.completed_tool_no_requeue",
                },
                {
                    "kind": "message",
                    "id": str(message_value(final_replies[0], "id")),
                    "assertion": "exactly one liveness reply after restart",
                    "scenario_ref": "L4.request.completed_tool_no_requeue",
                },
            ],
            l4_provider_token_split={
                "history_replay_tokens": first_post_restart_usage.input_tokens,
                "new_inference_tokens": first_post_restart_usage.output_tokens,
                "history_to_new_token_ratio": history_to_new_token_ratio,
                "source": "provider_reported_first_post_restart_call_proxy",
                "pre_restart_input_tokens": pre_restart_usage.input_tokens,
                "pre_restart_output_tokens": pre_restart_usage.output_tokens,
                "post_restart_input_tokens": post_restart_usage.input_tokens,
                "post_restart_output_tokens": post_restart_usage.output_tokens,
            },
        )


@pytest.mark.asyncio
@requires_e2e
async def test_l4_live_langgraph_cold_restart_no_duplicate_response_when_configured() -> (
    None
):
    blocked_reason = _l4_langgraph_live_blocked_reason()
    if blocked_reason:
        pytest.skip(blocked_reason)

    from tests.e2e.scenarios.test_langgraph_restart_rehydration import (
        run_langgraph_answers_down_message_once_after_restart,
    )

    await run_langgraph_answers_down_message_once_after_restart()
