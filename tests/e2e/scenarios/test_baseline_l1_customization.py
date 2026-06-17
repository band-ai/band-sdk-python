"""Gated live L1 customization scenario.

Run with:
    E2E_TESTS_ENABLED=true E2E_BASELINE_L1_LIVE=true \
        uv run pytest tests/e2e/scenarios/test_baseline_l1_customization.py -v -s --no-cov
"""

from __future__ import annotations

import os
import shlex
import shutil
from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest
from thenvoi_rest import AsyncRestClient

from thenvoi.agent import Agent
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import AdapterFeatures, Emit
from tests.baseline_l1_fixtures import (
    L1_CUSTOM_PROMPT_MARKER,
    L1_CUSTOM_RETURN_MARKER,
    L1_CUSTOM_TOOL_NAME,
    LogKeywordInput,
    make_l1_custom_tool_def,
    make_l1_langgraph_structured_tool,
    make_l1_pydantic_ai_tool,
)
from tests.e2e.adapters.conftest import (
    BASELINE_DEFAULT_PROVIDER_USAGE_BLOCKED_ADAPTER_NAMES,
    _require_codex_disposable_cwd,
    _require_gemini_key_or_vertex,
    _safe_approval_mode,
)
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
    fetch_chat_messages,
    message_ids,
    message_value,
    send_trigger_message,
    wait_for_new_agent_text_messages,
)

_STEP_TIMEOUT = 90.0
_CUSTOM_PROMPT = (
    f"Always include the word {L1_CUSTOM_PROMPT_MARKER} in every response you send."
)
_L1_SCENARIO_REFS = [
    "L1.request.custom_prompt_present",
    "L1.request.custom_prompt_additive",
    "L1.dispatch.custom_tool",
]


L1ToolHandler = Callable[[LogKeywordInput], Awaitable[dict[str, str]]]
L1AdapterFactory = Callable[[E2ESettings, L1ToolHandler], SimpleAdapter[Any]]


def _create_l1_anthropic_adapter(
    settings: E2ESettings,
    handler: L1ToolHandler,
) -> SimpleAdapter[Any]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set for Anthropic L1 live flow")
    from thenvoi.adapters.anthropic import AnthropicAdapter

    return AnthropicAdapter(
        model=settings.e2e_anthropic_model,
        prompt=_CUSTOM_PROMPT,
        additional_tools=[make_l1_custom_tool_def(handler)],
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )


def _create_l1_claude_sdk_adapter(
    settings: E2ESettings,
    handler: L1ToolHandler,
) -> SimpleAdapter[Any]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set for Claude SDK L1 live flow")
    from thenvoi.adapters.claude_sdk import ClaudeSDKAdapter

    return ClaudeSDKAdapter(
        model=settings.e2e_anthropic_model,
        custom_section=_CUSTOM_PROMPT,
        additional_tools=[make_l1_custom_tool_def(handler)],
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )


def _create_l1_langgraph_adapter(
    settings: E2ESettings,
    handler: L1ToolHandler,
) -> SimpleAdapter[Any]:
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set for LangGraph L1 live flow")
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import MemorySaver
    from thenvoi.adapters.langgraph import LangGraphAdapter

    return LangGraphAdapter(
        llm=ChatOpenAI(model=settings.e2e_llm_model),
        checkpointer=MemorySaver(),
        custom_section=_CUSTOM_PROMPT,
        additional_tools=[make_l1_langgraph_structured_tool(handler)],
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )


def _create_l1_pydantic_ai_adapter(
    settings: E2ESettings,
    handler: L1ToolHandler,
) -> SimpleAdapter[Any]:
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set for PydanticAI L1 live flow")
    from thenvoi.adapters.pydantic_ai import PydanticAIAdapter

    return PydanticAIAdapter(
        model=f"openai:{settings.e2e_llm_model}",
        custom_section=_CUSTOM_PROMPT,
        additional_tools=[make_l1_pydantic_ai_tool(handler)],
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )


def _create_l1_gemini_adapter(
    _settings: E2ESettings,
    handler: L1ToolHandler,
) -> SimpleAdapter[Any]:
    _require_gemini_key_or_vertex()
    from thenvoi.adapters.gemini import GeminiAdapter

    return GeminiAdapter(
        model=os.environ.get("E2E_GEMINI_MODEL", "gemini-2.5-flash"),
        prompt=_CUSTOM_PROMPT,
        additional_tools=[make_l1_custom_tool_def(handler)],
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )


def _create_l1_google_adk_adapter(
    _settings: E2ESettings,
    handler: L1ToolHandler,
) -> SimpleAdapter[Any]:
    _require_gemini_key_or_vertex()
    from thenvoi.adapters.google_adk import GoogleADKAdapter

    return GoogleADKAdapter(
        model=os.environ.get("E2E_GEMINI_MODEL", "gemini-2.5-flash"),
        custom_section=_CUSTOM_PROMPT,
        additional_tools=[make_l1_custom_tool_def(handler)],
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )


def _create_l1_codex_adapter(
    settings: E2ESettings,
    handler: L1ToolHandler,
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
    cwd = _require_codex_disposable_cwd()

    return CodexAdapter(
        config=CodexAdapterConfig(
            transport=cast(Any, transport),
            codex_command=command,
            codex_ws_url=os.environ.get("CODEX_WS_URL", "ws://127.0.0.1:8765"),
            model=os.environ.get("CODEX_MODEL", settings.e2e_llm_model),
            cwd=cwd,
            approval_policy=os.environ.get("CODEX_APPROVAL_POLICY", "never"),
            approval_mode=cast(
                Any,
                _safe_approval_mode(
                    adapter_name="Codex",
                    env_var="CODEX_APPROVAL_MODE",
                    default="manual",
                ),
            ),
            custom_section=_CUSTOM_PROMPT,
            enable_task_events=False,
            enable_execution_reporting=False,
        ),
        additional_tools=[make_l1_custom_tool_def(handler)],
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )


def _create_l1_opencode_adapter(
    settings: E2ESettings,
    handler: L1ToolHandler,
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
            approval_mode=_safe_approval_mode(
                adapter_name="OpenCode",
                env_var="OPENCODE_APPROVAL_MODE",
                default="auto_decline",
            ),
            question_mode=os.environ.get("OPENCODE_QUESTION_MODE", "auto_reject"),
        ),
        additional_tools=[make_l1_custom_tool_def(handler)],
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )


_L1_CUSTOM_TOOL_ADAPTERS: tuple[tuple[str, L1AdapterFactory], ...] = (
    ("anthropic", _create_l1_anthropic_adapter),
    ("claude_sdk", _create_l1_claude_sdk_adapter),
    ("langgraph", _create_l1_langgraph_adapter),
    ("pydantic_ai", _create_l1_pydantic_ai_adapter),
    ("gemini", _create_l1_gemini_adapter),
    ("google_adk", _create_l1_google_adk_adapter),
    ("codex", _create_l1_codex_adapter),
    ("opencode", _create_l1_opencode_adapter),
)
_L1_PROVIDER_USAGE_ADAPTERS: tuple[tuple[str, L1AdapterFactory], ...] = tuple(
    (adapter_name, factory)
    for adapter_name, factory in _L1_CUSTOM_TOOL_ADAPTERS
    if provider_usage_blocked_reason(adapter_name) is None
)
_L1_PROVIDER_USAGE_BLOCKED_ADAPTER_NAMES: tuple[str, ...] = tuple(
    dict.fromkeys(
        [
            *(
                adapter_name
                for adapter_name, _factory in _L1_CUSTOM_TOOL_ADAPTERS
                if adapter_name not in dict(_L1_PROVIDER_USAGE_ADAPTERS)
            ),
            *BASELINE_DEFAULT_PROVIDER_USAGE_BLOCKED_ADAPTER_NAMES,
        ]
    )
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
    custom_message = "M1_PROBE"
    calls: list[LogKeywordInput] = []

    async def log_keyword(args: LogKeywordInput) -> dict[str, str]:
        calls.append(args)
        return {"keyword": L1_CUSTOM_RETURN_MARKER}

    adapter = adapter_factory(e2e_config, log_keyword)
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
        step_1_prompt = (
            f"use the {L1_CUSTOM_TOOL_NAME} tool with the message "
            f"{custom_message!r} and tell me the exact keyword it returned"
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
        output_texts.extend(
            str(message_value(message, "content") or "") for message in step_1_replies
        )
        assert any(call.message == custom_message for call in calls), calls
        assert_content_contains(step_1_replies, L1_CUSTOM_RETURN_MARKER)
        assert_content_contains(step_1_replies, L1_CUSTOM_PROMPT_MARKER)

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
    platform_messages = [
        message
        for message in agent_text_messages(step_2_replies, agent_id)
        if user_name.lower() in str(message_value(message, "content") or "").lower()
        and L1_CUSTOM_PROMPT_MARKER in str(message_value(message, "content") or "")
    ]
    assert platform_messages, [
        message_value(message, "content") for message in step_2_replies
    ]
    assert not any(
        "Echo" in str(message_value(message, "content") or "")
        for message in step_2_replies
    ), [message_value(message, "content") for message in step_2_replies]
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
            "L1.request.custom_prompt_present": {
                "custom_prompt_marker": L1_CUSTOM_PROMPT_MARKER,
                "custom_prompt_marker_seen_in_steps": ["custom_tool", "room_listing"],
            },
            "L1.request.custom_prompt_additive": {
                "platform_live_user_seen": True,
                "platform_non_participant_absent": True,
                "platform_observation_source": "live_room_answer",
            },
            "L1.dispatch.custom_tool": {
                "custom_tool_name": L1_CUSTOM_TOOL_NAME,
                "custom_tool_args": {"message": custom_message},
                "custom_tool_calls": len(calls),
                "custom_tool_return_seen": True,
            },
        },
        platform_observations=[
            {
                "kind": "message",
                "id": str(message_value(step_1_replies[0], "id")),
                "assertion": "custom tool return and custom prompt marker reached user",
                "scenario_refs": [
                    "L1.request.custom_prompt_present",
                    "L1.dispatch.custom_tool",
                ],
            },
            {
                "kind": "message",
                "id": str(message_value(platform_messages[0], "id")),
                "assertion": "platform roster reply contained live room data and prompt marker",
                "scenario_ref": "L1.request.custom_prompt_additive",
            },
        ],
    )
