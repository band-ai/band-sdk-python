"""Gated live L4 cold-restart scenarios.

Run with:
    E2E_TESTS_ENABLED=true E2E_BASELINE_L4_LIVE=true LANGGRAPH_RESTART_SMOKE=true \
        uv run pytest tests/e2e/scenarios/test_baseline_l4_rehydration.py -v -s --no-cov
"""

from __future__ import annotations

import os

import pytest
from thenvoi_rest import AsyncRestClient
from thenvoi_rest.types import ParticipantRequest

from thenvoi.agent import Agent

from tests.e2e.adapters.conftest import (
    AdapterFactory,
    PROVIDER_USAGE_ADAPTER_FACTORIES,
    PROVIDER_USAGE_BLOCKED_ADAPTER_NAMES,
)
from tests.e2e.baseline_artifacts import (
    aggregate_provider_usage,
    baseline_pricing_from_env,
    start_baseline_tier2_timer,
    write_baseline_tier2_artifact,
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
    wait_for_new_agent_text_messages,
    wait_until_participant_absent,
    wait_until_participant_present,
)

_STEP_TIMEOUT = 90.0
_L4_SCENARIO_REFS = ["L4.request.cold_start_history"]


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


_L4_LIVE_BLOCKED_REASON = _l4_live_blocked_reason()
pytestmark = pytest.mark.skipif(
    _L4_LIVE_BLOCKED_REASON is not None,
    reason=_L4_LIVE_BLOCKED_REASON or "tier2_blocked: unknown L4 live block",
)


@pytest.fixture(
    params=tuple(PROVIDER_USAGE_ADAPTER_FACTORIES.items()),
    ids=lambda item: item[0],
)
def adapter_entry(request: pytest.FixtureRequest) -> tuple[str, AdapterFactory]:
    adapter_name, factory = request.param
    return str(adapter_name), factory


@pytest.mark.parametrize("adapter_name", PROVIDER_USAGE_BLOCKED_ADAPTER_NAMES)
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
@pytest.mark.timeout(600)
@requires_e2e
async def test_l4_live_adapter_cold_restart_rehydrates_without_replaying_invite_when_configured(
    e2e_config: E2ESettings,
    adapter_entry: tuple[str, AdapterFactory],
    api_client: AsyncRestClient,
    e2e_adapter_room: tuple[str, str, str],
    e2e_adapter_agent_credentials: E2EAgentCredentials,
) -> None:
    blocked_reason = _l4_live_blocked_reason()
    if blocked_reason:
        pytest.skip(blocked_reason)

    adapter_name, factory = adapter_entry
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

    first_adapter = factory(e2e_config)
    first_adapter.clear_provider_usage()
    first_agent = Agent.create(
        adapter=first_adapter,
        agent_id=agent_id,
        api_key=e2e_adapter_agent_credentials.api_key,
        ws_url=e2e_config.thenvoi_ws_url,
        rest_url=e2e_config.thenvoi_base_url,
    )
    async with first_agent:
        before_step_1 = message_ids(await fetch_chat_messages(api_client, chat_id))
        step_1_prompt = (
            "for later: the three keywords are ELEPHANT SAXOPHONE MIDNIGHT "
            "and my favorite color is TURQUOISE — then invite Echo to this room"
        )
        input_texts.append(step_1_prompt)
        await send_trigger_message(
            api_client,
            chat_id,
            step_1_prompt,
            agent_name,
            agent_id,
        )
        step_1_replies = await wait_for_new_agent_text_messages(
            api_client,
            chat_id,
            agent_id,
            before_step_1,
            min_count=1,
            timeout=_STEP_TIMEOUT,
            quiet_after=3.0,
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
        await send_trigger_message(
            api_client,
            chat_id,
            marker_prompt,
            agent_name,
            agent_id,
        )
        marker_replies = await wait_for_new_agent_text_messages(
            api_client,
            chat_id,
            agent_id,
            before_marker,
            min_count=1,
            timeout=_STEP_TIMEOUT,
            quiet_after=3.0,
        )
        assert len(marker_replies) == 1, [
            message_value(message, "content") for message in marker_replies
        ]
        output_texts.extend(
            str(message_value(message, "content") or "") for message in marker_replies
        )
        assert_content_contains(marker_replies, "UNIQUE_MARKER_789")

    await api_client.human_api_participants.remove_my_chat_participant(chat_id, echo_id)
    await wait_until_participant_absent(api_client, chat_id, echo_id, _STEP_TIMEOUT)

    restart_prompt = (
        "what were the three keywords and favorite color I gave you earlier?"
    )
    input_texts.append(restart_prompt)
    before_restart = message_ids(await fetch_chat_messages(api_client, chat_id))
    await send_trigger_message(
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
        restart_replies = await wait_for_new_agent_text_messages(
            api_client,
            chat_id,
            agent_id,
            before_restart,
            min_count=1,
            timeout=_STEP_TIMEOUT,
            quiet_after=3.0,
        )
        assert len(restart_replies) == 1, [
            message_value(message, "content") for message in restart_replies
        ]
        output_texts.extend(
            str(message_value(message, "content") or "") for message in restart_replies
        )
        for term in ("ELEPHANT", "SAXOPHONE", "MIDNIGHT", "TURQUOISE"):
            assert_content_contains(restart_replies, term)
        assert echo_id not in await participant_ids(api_client, chat_id)
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
        assert _marker_count(all_agent_messages_after_restart, "UNIQUE_MARKER_789") == 1
        assert _marker_count(post_restart_agent_messages, "UNIQUE_MARKER_789") == 0
        assert not any(
            echo_id in mention_ids(message) for message in post_restart_agent_messages
        )

        before_are_you_there = message_ids(
            await fetch_chat_messages(api_client, chat_id)
        )
        liveness_prompt = "are you there?"
        input_texts.append(liveness_prompt)
        await send_trigger_message(
            api_client,
            chat_id,
            liveness_prompt,
            agent_name,
            agent_id,
        )
        final_replies = await wait_for_new_agent_text_messages(
            api_client,
            chat_id,
            agent_id,
            before_are_you_there,
            min_count=1,
            timeout=_STEP_TIMEOUT,
            quiet_after=3.0,
        )
        assert len(final_replies) == 1, [
            message_value(message, "content") for message in final_replies
        ]
        output_texts.extend(
            str(message_value(message, "content") or "") for message in final_replies
        )
        assert echo_id not in await participant_ids(api_client, chat_id)
        pre_restart_usage = aggregate_provider_usage(
            first_adapter.provider_usage_snapshots()
        )
        post_restart_usage = aggregate_provider_usage(
            restarted_adapter.provider_usage_snapshots()
        )
        write_baseline_tier2_artifact(
            scenario_id="L4.request.cold_start_history",
            scenario_refs=["L4.request.cold_start_history"],
            adapter=adapter_name,
            timer=timer,
            pricing=pricing,
            provider_usage=aggregate_provider_usage(
                [
                    *first_adapter.provider_usage_snapshots(),
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
                "restart_reply_count": len(restart_replies),
                "agent_marker_count_after_restart": _marker_count(
                    all_agent_messages_after_restart, "UNIQUE_MARKER_789"
                ),
                "post_restart_marker_reply_count": _marker_count(
                    post_restart_agent_messages, "UNIQUE_MARKER_789"
                ),
                "post_restart_echo_mentions": sum(
                    1
                    for message in post_restart_agent_messages
                    if echo_id in mention_ids(message)
                ),
                "liveness_reply_count": len(final_replies),
                "echo_absent_after_restart": echo_id
                not in await participant_ids(api_client, chat_id),
            },
            platform_observations=[
                {
                    "kind": "message",
                    "id": str(message_value(restart_replies[0], "id")),
                    "assertion": "exactly one post-restart hydrated recall reply",
                },
                {
                    "kind": "room_state",
                    "id": chat_id,
                    "assertion": "Echo absent after restart and no post-restart Echo mention",
                },
                {
                    "kind": "message",
                    "id": str(message_value(final_replies[0], "id")),
                    "assertion": "exactly one liveness reply after restart",
                },
            ],
            l4_provider_token_split={
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
