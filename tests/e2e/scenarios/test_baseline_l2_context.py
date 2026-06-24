"""Gated live L2 context-fidelity scenario.

Run with:
    E2E_TESTS_ENABLED=true E2E_BASELINE_L2_LIVE=true \
        uv run pytest tests/e2e/scenarios/test_baseline_l2_context.py -v -s --no-cov
"""

from __future__ import annotations

import pytest
from band_rest import AsyncRestClient

from band.agent import Agent

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
from tests.e2e.baseline_assertions import (
    assert_agent_responded,
    assert_recalled_at_least,
    content_of,
    wait_until_agent_quiescent,
)
from tests.e2e.baseline_settings import BaselineL2Settings
from tests.e2e.conftest import E2ESettings, requires_e2e
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    listening_for_agent_responses,
    message_value,
    send_trigger_message,
)

# Drain the burst to quiescence, then capture the recall turn event-driven —
# instead of polling a fixed full window. Adaptive + bounded, not 400s.
_REPLY_TIMEOUT = 60.0
_BURST_QUIET = 8.0
_BURST_MAX = 120.0
_RECALL_QUIET = 6.0
_RECALL_TERMS = ["ACME", "LIGHTHOUSE", "POSTGRESQL"]
_RECALL_MIN = 2
_L2_SCENARIO_REFS = [
    "L2.request.full_history",
    "L2.request.earliest_turn",
]


_L2_SETTINGS = BaselineL2Settings()
_L2_LIVE_BLOCKED_REASON = _L2_SETTINGS.blocked_reason()
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
    blocked_reason = _L2_SETTINGS.blocked_reason()
    if blocked_reason:
        pytest.skip(blocked_reason)
    blocked_reason = write_provider_usage_blocked_artifact_if_needed(
        scenario_id="L2.request.full_history",
        scenario_refs=_L2_SCENARIO_REFS,
        adapter=adapter_name,
    )
    assert blocked_reason is not None


@pytest.mark.asyncio
@pytest.mark.timeout(240)
@requires_e2e
async def test_l2_live_burst_history_recalls_planted_terms_when_configured(
    e2e_config: E2ESettings,
    l2_provider_usage_adapter_entry: tuple[str, AdapterFactory],
    e2e_fresh_room: tuple[str, str, str],
    e2e_agent_info: tuple[str, str],
    api_client: AsyncRestClient,
    e2e_unlimited_user_client: AsyncRestClient,
    ws_client: TrackingWebSocketClient,
) -> None:
    blocked_reason = _L2_SETTINGS.blocked_reason()
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
        api_key=e2e_config.band_api_key,
        ws_url=e2e_config.band_ws_url,
        rest_url=e2e_config.band_base_url,
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

    # Probe employer (ACME), not the planted name (MARCO): the planted facts are
    # spoken by the platform sender, so "what is my name" collides with the
    # sender's real identity. ACME lives in the same first planted message, so
    # this still exercises recall of the earliest burst turn without ambiguity.
    recall_prompt = (
        "where do I work, what project am I building, and what database do we use?"
    )

    async with agent:
        for message in planted_messages:
            await send_trigger_message(
                e2e_unlimited_user_client, chat_id, message, agent_name, agent_id
            )
        # Drain the burst fully before recall so trailing acks don't leak into
        # the recall turn. Tolerant: only require the agent engaged at all.
        burst_replies = await wait_until_agent_quiescent(
            api_client, chat_id, agent_id, quiet=_BURST_QUIET, max_wait=_BURST_MAX
        )
        assert_agent_responded(burst_replies, min_count=1)

        async with listening_for_agent_responses(
            ws_client,
            chat_id,
            timeout=_REPLY_TIMEOUT,
            min_messages=1,
            expected_agent_id=agent_id,
            quiet_after_first=_RECALL_QUIET,
        ) as wait:
            await send_trigger_message(
                api_client, chat_id, recall_prompt, agent_name, agent_id
            )
            recall_replies = await wait()

    assert_agent_responded(recall_replies, min_count=1)
    assert_recalled_at_least(recall_replies, _RECALL_TERMS, min_count=_RECALL_MIN)
    write_baseline_tier2_artifact(
        scenario_id="L2.request.full_history",
        scenario_refs=_L2_SCENARIO_REFS,
        adapter=adapter_name,
        timer=timer,
        pricing=pricing,
        provider_usage=provider_usage_from_adapter(adapter, adapter_name=adapter_name),
        input_texts=[*planted_messages, recall_prompt],
        output_texts=[
            content_of(message) for message in [*burst_replies, *recall_replies]
        ],
        observed_agent_text_message_count=len(burst_replies) + len(recall_replies),
        evidence={
            "L2.request.full_history": {
                "reply_timeout_seconds": _REPLY_TIMEOUT,
                "burst_quiet_window_seconds": _BURST_QUIET,
                "recall_quiet_window_seconds": _RECALL_QUIET,
                "burst_reply_count": len(burst_replies),
                "recall_reply_count": len(recall_replies),
                "recall_terms": _RECALL_TERMS,
                "recall_min": _RECALL_MIN,
            },
            "L2.request.earliest_turn": {
                "earliest_marker_recalled": "ACME",
                "reply_timeout_seconds": _REPLY_TIMEOUT,
            },
        },
        platform_observations=[
            {
                "kind": "message",
                "id": str(message_value(recall_replies[0], "id")),
                "assertion": (
                    f"recall reply contains >= {_RECALL_MIN} of "
                    f"{'/'.join(_RECALL_TERMS)}"
                ),
                "scenario_refs": _L2_SCENARIO_REFS,
            }
        ],
    )
