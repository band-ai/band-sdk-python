"""Gated live L2 context-fidelity scenario.

Run with:
    E2E_TESTS_ENABLED=true E2E_BASELINE_L2_LIVE=true \
        uv run pytest tests/e2e/scenarios/test_baseline_l2_context.py -v -s --no-cov
"""

from __future__ import annotations

import os

import pytest
from thenvoi_rest import AsyncRestClient

from thenvoi.agent import Agent

from tests.e2e.adapters.conftest import (
    AdapterFactory,
    BASELINE_DEFAULT_PROVIDER_USAGE_ADAPTER_FACTORIES,
    BASELINE_DEFAULT_PROVIDER_USAGE_BLOCKED_ADAPTER_NAMES,
)

from tests.e2e.baseline_artifacts import (
    baseline_pricing_from_env,
    provider_usage_from_adapter,
    start_baseline_tier2_timer,
    write_baseline_tier2_artifact,
    write_provider_usage_blocked_artifact_if_needed,
)
from tests.e2e.conftest import E2ESettings, requires_e2e
from tests.e2e.helpers import (
    assert_content_contains,
    fetch_chat_messages,
    message_ids,
    message_value,
    send_trigger_message,
    wait_full_window_for_new_agent_text_messages,
)

_BURST_TIMEOUT = 400.0
_STEP_TIMEOUT = 90.0
_L2_SCENARIO_REFS = [
    "L2.request.full_history",
    "L2.request.earliest_turn",
]


def _l2_live_blocked_reason() -> str | None:
    if os.environ.get("E2E_BASELINE_L2_LIVE") != "true":
        return "tier2_blocked: E2E_BASELINE_L2_LIVE=true not set for live L2 flow"
    return None


_L2_LIVE_BLOCKED_REASON = _l2_live_blocked_reason()
pytestmark = pytest.mark.skipif(
    _L2_LIVE_BLOCKED_REASON is not None,
    reason=_L2_LIVE_BLOCKED_REASON or "tier2_blocked: unknown L2 live block",
)


@pytest.fixture(
    params=tuple(BASELINE_DEFAULT_PROVIDER_USAGE_ADAPTER_FACTORIES.items()),
    ids=lambda item: item[0],
)
def l2_provider_usage_adapter_entry(
    request: pytest.FixtureRequest,
) -> tuple[str, AdapterFactory]:
    adapter_name, factory = request.param
    return str(adapter_name), factory


@pytest.mark.parametrize(
    "adapter_name", BASELINE_DEFAULT_PROVIDER_USAGE_BLOCKED_ADAPTER_NAMES
)
def test_l2_live_unsupported_adapter_rows_write_blocked_artifacts_when_configured(
    adapter_name: str,
) -> None:
    blocked_reason = _l2_live_blocked_reason()
    if blocked_reason:
        pytest.skip(blocked_reason)
    blocked_reason = write_provider_usage_blocked_artifact_if_needed(
        scenario_id="L2.request.full_history",
        scenario_refs=_L2_SCENARIO_REFS,
        adapter=adapter_name,
    )
    assert blocked_reason is not None


@pytest.mark.asyncio
@pytest.mark.timeout(650)
@requires_e2e
async def test_l2_live_burst_history_recalls_planted_terms_when_configured(
    e2e_config: E2ESettings,
    l2_provider_usage_adapter_entry: tuple[str, AdapterFactory],
    e2e_fresh_room: tuple[str, str, str],
    e2e_agent_info: tuple[str, str],
    api_client: AsyncRestClient,
    e2e_unlimited_user_client: AsyncRestClient,
) -> None:
    blocked_reason = _l2_live_blocked_reason()
    if blocked_reason:
        pytest.skip(blocked_reason)

    timer = start_baseline_tier2_timer()
    pricing = baseline_pricing_from_env()
    chat_id, _user_id, _user_name = e2e_fresh_room
    agent_id, agent_name = e2e_agent_info
    adapter_name, adapter_factory = l2_provider_usage_adapter_entry

    adapter = adapter_factory(e2e_config)
    adapter.clear_provider_usage()
    agent = Agent.create(
        adapter=adapter,
        agent_id=e2e_config.test_agent_id,
        api_key=e2e_config.thenvoi_api_key,
        ws_url=e2e_config.thenvoi_ws_url,
        rest_url=e2e_config.thenvoi_base_url,
    )

    planted_messages = [
        "my name is MARCO and I work at ACME",
        "I have a pet cat named WHISKERS",
        "my favorite programming language is RUST",
        "I'm building a project called LIGHTHOUSE",
        "the deadline for LIGHTHOUSE is the SOLSTICE-SPRINT",
        "my team has 7 people including SANDRA and KOJI",
        "we use POSTGRESQL for our database",
        "our office is in BARCELONA",
        "I'm learning to play the BANJO on weekends",
    ]

    async with agent:
        before_burst = message_ids(await fetch_chat_messages(api_client, chat_id))
        for message in planted_messages:
            await send_trigger_message(
                e2e_unlimited_user_client, chat_id, message, agent_name, agent_id
            )

        burst_replies = await wait_full_window_for_new_agent_text_messages(
            api_client,
            chat_id,
            agent_id,
            before_burst,
            timeout=_BURST_TIMEOUT,
        )
        assert len(burst_replies) == 9, [
            message_value(message, "content") for message in burst_replies
        ]

        before_recall = message_ids(await fetch_chat_messages(api_client, chat_id))
        recall_prompt = (
            "what is my name, what project am I building, and what database do we use?"
        )
        await send_trigger_message(
            api_client,
            chat_id,
            recall_prompt,
            agent_name,
            agent_id,
        )
        recall_replies = await wait_full_window_for_new_agent_text_messages(
            api_client,
            chat_id,
            agent_id,
            before_recall,
            timeout=_STEP_TIMEOUT,
        )

    assert len(recall_replies) == 1, [
        message_value(message, "content") for message in recall_replies
    ]
    for term in ("MARCO", "LIGHTHOUSE", "POSTGRESQL"):
        assert_content_contains(recall_replies, term)
    write_baseline_tier2_artifact(
        scenario_id="L2.request.full_history",
        scenario_refs=_L2_SCENARIO_REFS,
        adapter=adapter_name,
        timer=timer,
        pricing=pricing,
        provider_usage=provider_usage_from_adapter(adapter, adapter_name=adapter_name),
        input_texts=[*planted_messages, recall_prompt],
        output_texts=[
            str(message_value(message, "content") or "")
            for message in [*burst_replies, *recall_replies]
        ],
        observed_agent_text_message_count=len(burst_replies) + len(recall_replies),
        evidence={
            "L2.request.full_history": {
                "burst_observation_window_seconds": _BURST_TIMEOUT,
                "recall_observation_window_seconds": _STEP_TIMEOUT,
                "burst_reply_count": len(burst_replies),
                "recall_reply_count": len(recall_replies),
                "recalled_terms": ["MARCO", "LIGHTHOUSE", "POSTGRESQL"],
            },
            "L2.request.earliest_turn": {
                "earliest_marker_recalled": "MARCO",
                "recall_observation_window_seconds": _STEP_TIMEOUT,
            },
        },
        platform_observations=[
            {
                "kind": "message",
                "id": str(message_value(recall_replies[0], "id")),
                "assertion": "single recall reply contains MARCO/LIGHTHOUSE/POSTGRESQL",
                "scenario_refs": _L2_SCENARIO_REFS,
            }
        ],
    )
