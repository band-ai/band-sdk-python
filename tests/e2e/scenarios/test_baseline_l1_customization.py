"""Gated live L1 customization scenario.

Run with:
    E2E_TESTS_ENABLED=true E2E_BASELINE_L1_LIVE=true \
        uv run pytest tests/e2e/scenarios/test_baseline_l1_customization.py -v -s --no-cov
"""

from __future__ import annotations

import os
import shlex
import shutil
from collections.abc import Callable
from typing import Any, cast

import pytest
from pydantic import BaseModel
from thenvoi_rest import AsyncRestClient

from thenvoi.agent import Agent
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import AdapterFeatures, Emit
from thenvoi.runtime.custom_tools import CustomToolDef

from tests.e2e.baseline_artifacts import (
    baseline_pricing_from_env,
    provider_usage_blocked_reason,
    provider_usage_from_adapter,
    start_baseline_tier2_timer,
    write_baseline_tier2_artifact,
    write_provider_usage_blocked_artifact_if_needed,
)
from tests.e2e.conftest import E2ESettings, requires_e2e
from tests.e2e.helpers import (
    agent_text_messages,
    assert_content_contains,
    assert_no_content_contains,
    fetch_chat_messages,
    message_ids,
    message_value,
    send_trigger_message,
    wait_for_new_agent_text_messages,
)

_STEP_TIMEOUT = 90.0
_CUSTOM_PROMPT = "Always include the word SNOLLYGOSTER in every response you send."
_L1_SCENARIO_REFS = [
    "L1.request.custom_prompt_present",
    "L1.request.custom_prompt_additive",
    "L1.dispatch.custom_tool",
]


class LogKeywordInput(BaseModel):
    """Input for the L1 custom tool proof."""

    message: str


L1AdapterFactory = Callable[[E2ESettings, list[CustomToolDef]], SimpleAdapter[Any]]


def _create_l1_anthropic_adapter(
    settings: E2ESettings,
    custom_tools: list[CustomToolDef],
) -> SimpleAdapter[Any]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set for Anthropic L1 live flow")
    from thenvoi.adapters.anthropic import AnthropicAdapter

    return AnthropicAdapter(
        model=settings.e2e_anthropic_model,
        prompt=_CUSTOM_PROMPT,
        additional_tools=custom_tools,
    )


def _create_l1_claude_sdk_adapter(
    settings: E2ESettings,
    custom_tools: list[CustomToolDef],
) -> SimpleAdapter[Any]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set for Claude SDK L1 live flow")
    from thenvoi.adapters.claude_sdk import ClaudeSDKAdapter

    return ClaudeSDKAdapter(
        model=settings.e2e_anthropic_model,
        custom_section=_CUSTOM_PROMPT,
        additional_tools=custom_tools,
    )


def _create_l1_codex_adapter(
    settings: E2ESettings,
    custom_tools: list[CustomToolDef],
) -> SimpleAdapter[Any]:
    from thenvoi.adapters.codex import CodexAdapter, CodexAdapterConfig

    transport = os.environ.get("CODEX_TRANSPORT", "stdio")
    if transport not in {"stdio", "ws"}:
        pytest.skip("CODEX_TRANSPORT must be 'stdio' or 'ws' for Codex L1 live flow")

    command_text = os.environ.get("CODEX_COMMAND")
    command = tuple(shlex.split(command_text)) if command_text else None
    binary = command[0] if command else "codex"
    if transport == "stdio" and not shutil.which(binary):
        pytest.skip("Codex L1 live flow requires the codex CLI on PATH")

    return CodexAdapter(
        config=CodexAdapterConfig(
            transport=cast(Any, transport),
            codex_command=command,
            codex_ws_url=os.environ.get("CODEX_WS_URL", "ws://127.0.0.1:8765"),
            model=os.environ.get("CODEX_MODEL", settings.e2e_llm_model),
            cwd=os.environ.get("CODEX_CWD", os.getcwd()),
            approval_policy=os.environ.get("CODEX_APPROVAL_POLICY", "never"),
            approval_mode=cast(
                Any, os.environ.get("CODEX_APPROVAL_MODE", "auto_accept")
            ),
            custom_section=_CUSTOM_PROMPT,
            enable_task_events=False,
            enable_execution_reporting=False,
        ),
        additional_tools=custom_tools,
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )


def _create_l1_opencode_adapter(
    settings: E2ESettings,
    custom_tools: list[CustomToolDef],
) -> SimpleAdapter[Any]:
    base_url = os.environ.get("OPENCODE_BASE_URL")
    if not base_url:
        pytest.skip("OPENCODE_BASE_URL not set for OpenCode L1 live flow")
    from thenvoi.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig

    return OpencodeAdapter(
        config=OpencodeAdapterConfig(
            base_url=base_url,
            provider_id=os.environ.get("OPENCODE_PROVIDER_ID", "opencode"),
            model_id=os.environ.get("OPENCODE_MODEL_ID", "minimax-m2.5-free"),
            agent=os.environ.get("OPENCODE_AGENT") or None,
            custom_section=_CUSTOM_PROMPT,
            approval_mode="auto_accept",
            question_mode="auto_reject",
        ),
        additional_tools=custom_tools,
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )


_L1_CUSTOM_TOOL_ADAPTERS: tuple[tuple[str, L1AdapterFactory], ...] = (
    ("anthropic", _create_l1_anthropic_adapter),
    ("claude_sdk", _create_l1_claude_sdk_adapter),
    ("codex", _create_l1_codex_adapter),
    ("opencode", _create_l1_opencode_adapter),
)
_L1_PROVIDER_USAGE_ADAPTERS: tuple[tuple[str, L1AdapterFactory], ...] = tuple(
    (adapter_name, factory)
    for adapter_name, factory in _L1_CUSTOM_TOOL_ADAPTERS
    if provider_usage_blocked_reason(adapter_name) is None
)
_L1_PROVIDER_USAGE_BLOCKED_ADAPTER_NAMES: tuple[str, ...] = tuple(
    adapter_name
    for adapter_name, _factory in _L1_CUSTOM_TOOL_ADAPTERS
    if adapter_name not in dict(_L1_PROVIDER_USAGE_ADAPTERS)
)


@pytest.fixture(params=_L1_PROVIDER_USAGE_ADAPTERS, ids=lambda item: item[0])
def l1_custom_adapter_entry(
    request: pytest.FixtureRequest,
) -> tuple[str, L1AdapterFactory]:
    adapter_name, factory = request.param
    return str(adapter_name), factory


@pytest.mark.parametrize("adapter_name", _L1_PROVIDER_USAGE_BLOCKED_ADAPTER_NAMES)
def test_l1_live_unsupported_adapter_rows_write_blocked_artifacts_when_configured(
    adapter_name: str,
) -> None:
    blocked_reason = _l1_live_blocked_reason()
    if blocked_reason:
        pytest.skip(blocked_reason)
    blocked_reason = write_provider_usage_blocked_artifact_if_needed(
        scenario_id="L1.request.custom_prompt_present",
        scenario_refs=_L1_SCENARIO_REFS,
        adapter=adapter_name,
    )
    assert blocked_reason is not None


def _l1_live_blocked_reason() -> str | None:
    if os.environ.get("E2E_BASELINE_L1_LIVE") != "true":
        return "tier2_blocked: E2E_BASELINE_L1_LIVE=true not set for live L1 flow"
    return None


_L1_LIVE_BLOCKED_REASON = _l1_live_blocked_reason()
pytestmark = pytest.mark.skipif(
    _L1_LIVE_BLOCKED_REASON is not None,
    reason=_L1_LIVE_BLOCKED_REASON or "tier2_blocked: unknown L1 live block",
)


@pytest.mark.asyncio
@pytest.mark.timeout(240)
@requires_e2e
async def test_l1_live_custom_prompt_tool_and_platform_tool_survive_when_configured(
    e2e_config: E2ESettings,
    l1_custom_adapter_entry: tuple[str, L1AdapterFactory],
    e2e_fresh_room: tuple[str, str, str],
    e2e_agent_info: tuple[str, str],
    api_client: AsyncRestClient,
) -> None:
    blocked_reason = _l1_live_blocked_reason()
    if blocked_reason:
        pytest.skip(blocked_reason)

    timer = start_baseline_tier2_timer()
    pricing = baseline_pricing_from_env()
    input_texts: list[str] = []
    output_texts: list[str] = []
    chat_id, _user_id, user_name = e2e_fresh_room
    agent_id, agent_name = e2e_agent_info
    adapter_name, adapter_factory = l1_custom_adapter_entry
    calls: list[LogKeywordInput] = []

    async def log_keyword(args: LogKeywordInput) -> dict[str, str]:
        calls.append(args)
        return {"keyword": "FLIBBERTIGIBBET"}

    adapter = adapter_factory(e2e_config, [(LogKeywordInput, log_keyword)])
    adapter.clear_provider_usage()
    agent = Agent.create(
        adapter=adapter,
        agent_id=e2e_config.test_agent_id,
        api_key=e2e_config.thenvoi_api_key,
        ws_url=e2e_config.thenvoi_ws_url,
        rest_url=e2e_config.thenvoi_base_url,
    )

    async with agent:
        before_step_1 = message_ids(await fetch_chat_messages(api_client, chat_id))
        step_1_prompt = 'use the log keyword tool with the message "M1_PROBE" and tell me the exact keyword it returned'
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
        output_texts.extend(
            str(message_value(message, "content") or "") for message in step_1_replies
        )
        assert any(call.message == "M1_PROBE" for call in calls), calls
        assert_content_contains(step_1_replies, "FLIBBERTIGIBBET")
        assert_content_contains(step_1_replies, "SNOLLYGOSTER")

        before_step_2 = message_ids(await fetch_chat_messages(api_client, chat_id))
        step_2_prompt = "list the names of everyone in this room"
        input_texts.append(step_2_prompt)
        await send_trigger_message(
            api_client,
            chat_id,
            step_2_prompt,
            agent_name,
            agent_id,
        )
        step_2_replies = await wait_for_new_agent_text_messages(
            api_client,
            chat_id,
            agent_id,
            before_step_2,
            min_count=1,
            timeout=_STEP_TIMEOUT,
            quiet_after=3.0,
        )

    output_texts.extend(
        str(message_value(message, "content") or "") for message in step_2_replies
    )
    assert any(
        user_name.lower() in str(message_value(message, "content") or "").lower()
        and "SNOLLYGOSTER" in str(message_value(message, "content") or "")
        for message in agent_text_messages(step_2_replies, agent_id)
    ), [message_value(message, "content") for message in step_2_replies]
    assert_no_content_contains(step_2_replies, "Echo")
    write_baseline_tier2_artifact(
        scenario_id="L1.request.custom_prompt_present",
        scenario_refs=_L1_SCENARIO_REFS,
        adapter=adapter_name,
        timer=timer,
        pricing=pricing,
        provider_usage=provider_usage_from_adapter(adapter, adapter_name=adapter_name),
        input_texts=input_texts,
        output_texts=output_texts,
        observed_agent_text_message_count=len(step_1_replies) + len(step_2_replies),
        evidence={
            "custom_tool_calls": len(calls),
            "custom_prompt_keyword_seen": any(
                "SNOLLYGOSTER" in str(message_value(message, "content") or "")
                for message in [*step_1_replies, *step_2_replies]
            ),
            "platform_tool_room_listing_replied": len(step_2_replies),
        },
        platform_observations=[
            {
                "kind": "message",
                "id": str(message_value(step_1_replies[0], "id")),
                "assertion": "custom tool return and custom prompt marker reached user",
            },
            {
                "kind": "message",
                "id": str(message_value(step_2_replies[0], "id")),
                "assertion": "platform room-listing tool still works under custom config",
            },
        ],
    )
