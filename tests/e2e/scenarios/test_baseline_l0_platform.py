"""Gated live L0 platform-adaptation scenario.

Run with:
    E2E_TESTS_ENABLED=true E2E_BASELINE_L0_LIVE=true \
        E2E_ECHO_AGENT_ID=<uuid> E2E_ECHO_AGENT_NAME=<name> E2E_ECHO_AGENT_HANDLE=<handle> \
        uv run pytest tests/e2e/scenarios/test_baseline_l0_platform.py -v -s --no-cov
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any

import pytest
from band_rest import AsyncRestClient
from band_rest.types import ParticipantRequest

from band.agent import Agent
from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AdapterFeatures, Emit, PlatformMessage
from band.runtime.types import AgentConfig

from tests.e2e.adapters.conftest import (
    AdapterFactory,
    BASELINE_L0_ADAPTER_FACTORIES,
    BASELINE_L0_BLOCKED_ADAPTER_NAMES,
)
from tests.e2e.baseline_artifacts import (
    baseline_pricing_from_env,
    l0_usage_from_live_observation,
    start_baseline_tier2_timer,
    write_baseline_tier2_artifact,
    write_baseline_tier2_blocked_artifact,
)
from tests.e2e.baseline_settings import BaselineL0Settings
from tests.e2e.conftest import E2EAgentCredentials, E2ESettings, requires_e2e
from tests.e2e.helpers import (
    ToolObservationUnavailableError,
    agent_text_messages,
    assert_content_contains,
    fetch_chat_messages,
    mention_ids,
    message_ids,
    message_value,
    participant_ids,
    send_trigger_message,
    wait_for_chat_messages,
    wait_for_new_agent_text_messages,
    wait_for_required_tool_observations,
    wait_until_participant_absent,
    wait_until_participant_present,
)

_STEP_TIMEOUT = 90.0
_LOOP_SUPPRESSION_WINDOW = 90.0
_L0_SCENARIO_REFS = [
    "L0.request.platform_context",
    "L0.request.history",
    "L0.request.participants",
    "L0.dispatch.send_message",
    "L0.dispatch.add_participant",
    "L0.dispatch.remove_participant",
    "L0.dispatch.get_participants",
    "L0.dispatch.lookup_peers",
]


_L0_SETTINGS = BaselineL0Settings()
_L0_LIVE_BLOCKED_REASON = _L0_SETTINGS.blocked_reason()
pytestmark = pytest.mark.skipif(
    _L0_LIVE_BLOCKED_REASON is not None,
    reason=_L0_LIVE_BLOCKED_REASON or "tier2_blocked: unknown L0 live block",
)


@pytest.fixture(
    params=tuple(BASELINE_L0_ADAPTER_FACTORIES.items()),
    ids=lambda item: item[0],
)
def adapter_entry(request: pytest.FixtureRequest) -> tuple[str, AdapterFactory]:
    adapter_name, factory = request.param
    return str(adapter_name), factory


@pytest.mark.parametrize("adapter_name", BASELINE_L0_BLOCKED_ADAPTER_NAMES)
def test_l0_live_unsupported_adapter_rows_write_blocked_artifacts_when_configured(
    adapter_name: str,
) -> None:
    blocked_reason = _L0_SETTINGS.blocked_reason()
    if blocked_reason:
        pytest.skip(blocked_reason)
    reason = (
        "tier2_blocked: adapter does not have a baseline L0 full-flow live factory "
        f"in this dependency lane: {adapter_name}"
    )
    write_baseline_tier2_blocked_artifact(
        scenario_id="L0.request.platform_context",
        scenario_refs=_L0_SCENARIO_REFS,
        adapter=adapter_name,
        reason=reason,
    )


class _DeterministicEchoAdapter(SimpleAdapter[Any]):
    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: Any,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        del history, participants_msg, contacts_msg, is_session_bootstrap, room_id
        participants = await tools.get_participants()
        sender_handle = next(
            (
                participant.handle
                for participant in participants
                if participant.id == msg.sender_id and participant.handle
            ),
            None,
        )
        if sender_handle is None:
            raise AssertionError(
                f"No participant handle found for sender {msg.sender_id}"
            )
        content = "ECHO: HELLO_ECHO" if "HELLO_ECHO" in msg.content else msg.content
        await tools.send_message(
            content=content,
            mentions=[f"@{sender_handle.lstrip('@')}"],
        )


def _labeled_line(content: str, label: str) -> str:
    prefix = f"{label}:"
    for line in content.splitlines():
        if line.strip().lower().startswith(prefix.lower()):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"Missing {label}: line in reply: {content}")


async def _wait_for_required_tool_observations_or_block(
    client: AsyncRestClient,
    *,
    room_id: str,
    agent_id: str,
    after_message_id: str,
    required_tool_names: set[str],
    timeout: float,
    adapter_name: str,
) -> list[Any]:
    try:
        return await wait_for_required_tool_observations(
            client,
            room_id=room_id,
            agent_id=agent_id,
            after_message_id=after_message_id,
            required_tool_names=required_tool_names,
            timeout=timeout,
        )
    except ToolObservationUnavailableError as exc:
        reason = str(exc)
        write_baseline_tier2_blocked_artifact(
            scenario_id="L0.request.platform_context",
            scenario_refs=_L0_SCENARIO_REFS,
            adapter=adapter_name,
            reason=reason,
        )
        pytest.skip(reason)


def _tool_observation_records(
    observations: list[Any], assertion: str
) -> list[dict[str, Any]]:
    return [
        {
            "kind": "tool_execution_event",
            "id": observation.event_id,
            "tool_name": observation.tool_name,
            "message_type": observation.message_type,
            "tool_call_id": observation.tool_call_id,
            "assertion": assertion,
        }
        for observation in observations
    ]


def _enable_execution_reporting_or_block(
    adapter: SimpleAdapter[Any],
    *,
    adapter_name: str,
) -> str | None:
    if Emit.EXECUTION not in adapter.SUPPORTED_EMIT:
        return (
            "tier2_blocked: adapter does not expose live tool execution events "
            f"for baseline L0 proof: {adapter_name}"
        )
    existing = adapter.features
    adapter.features = AdapterFeatures(
        capabilities=existing.capabilities,
        emit={*existing.emit, Emit.EXECUTION},
        include_tools=existing.include_tools,
        exclude_tools=existing.exclude_tools,
        include_categories=existing.include_categories,
    )
    return None


async def _agent_messages_after(
    client: AsyncRestClient,
    chat_id: str,
    agent_id: str,
    *,
    after_message: Any,
    duration: float,
) -> list[Any]:
    after_id = str(message_value(after_message, "id"))
    deadline = asyncio.get_running_loop().time() + duration
    current: list[Any] = []
    while asyncio.get_running_loop().time() < deadline:
        messages = await fetch_chat_messages(client, chat_id)
        boundary_index = next(
            (
                index
                for index, message in enumerate(messages)
                if str(message_value(message, "id")) == after_id
            ),
            None,
        )
        if boundary_index is not None:
            current = agent_text_messages(messages[:boundary_index], agent_id)
        await asyncio.sleep(0.5)
    return current


@pytest.mark.asyncio
@pytest.mark.timeout(500)
@requires_e2e
async def test_l0_live_identity_context_echo_loop_and_remove_when_configured(
    e2e_config: E2ESettings,
    adapter_entry: tuple[str, AdapterFactory],
    e2e_fresh_adapter_room: tuple[str, str, str],
    api_client: AsyncRestClient,
    e2e_adapter_agent_credentials: E2EAgentCredentials,
) -> None:
    blocked_reason = _L0_SETTINGS.blocked_reason()
    if blocked_reason:
        pytest.skip(blocked_reason)

    adapter_name, factory = adapter_entry
    timer = start_baseline_tier2_timer()
    pricing = baseline_pricing_from_env()
    input_texts: list[str] = []
    output_texts: list[str] = []
    observed_agent_text_message_count = 0
    chat_id, _user_id, user_name = e2e_fresh_adapter_room
    agent_id = e2e_adapter_agent_credentials.agent_id
    agent_name = e2e_adapter_agent_credentials.name
    echo_id = _L0_SETTINGS.echo.id
    echo_api_key = _L0_SETTINGS.echo.api_key
    echo_name = _L0_SETTINGS.echo.name
    echo_handle = _L0_SETTINGS.echo.visible_handle

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

    adapter = factory(e2e_config)
    execution_blocked_reason = _enable_execution_reporting_or_block(
        adapter,
        adapter_name=adapter_name,
    )
    if execution_blocked_reason is not None:
        write_baseline_tier2_blocked_artifact(
            scenario_id="L0.request.platform_context",
            scenario_refs=_L0_SCENARIO_REFS,
            adapter=adapter_name,
            reason=execution_blocked_reason,
        )
        pytest.skip(execution_blocked_reason)
    adapter.clear_provider_usage()
    agent_observation_client = AsyncRestClient(
        api_key=e2e_adapter_agent_credentials.api_key,
        base_url=e2e_config.band_base_url,
    )
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=e2e_adapter_agent_credentials.api_key,
        ws_url=e2e_config.band_ws_url,
        rest_url=e2e_config.band_base_url,
    )
    echo_agent = Agent.create(
        adapter=_DeterministicEchoAdapter(),
        agent_id=echo_id,
        api_key=echo_api_key,
        ws_url=e2e_config.band_ws_url,
        rest_url=e2e_config.band_base_url,
        config=AgentConfig(auto_subscribe_existing_rooms=False),
    )

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(echo_agent)
        await stack.enter_async_context(agent)

        before_step_1 = message_ids(await fetch_chat_messages(api_client, chat_id))
        assert before_step_1 == set(), (
            "fresh adapter-owned L0 room already contains durable messages: "
            f"{before_step_1}"
        )
        step_1_prompt = (
            "tell me your name and which chat we are in. Then reply with exactly "
            "two labeled lines: CURRENT_ROOM: the people currently in this room; "
            "INVITABLE_PEERS: the people you could invite but who are not currently "
            "in this room."
        )
        input_texts.append(step_1_prompt)
        step_1_trigger_id = await send_trigger_message(
            api_client,
            chat_id,
            step_1_prompt,
            agent_name,
            agent_id,
        )
        identity_replies = await wait_for_new_agent_text_messages(
            api_client,
            chat_id,
            agent_id,
            before_step_1,
            min_count=1,
            timeout=_STEP_TIMEOUT,
            quiet_after=3.0,
        )
        assert len(identity_replies) == 1, [
            message_value(message, "content") for message in identity_replies
        ]
        observed_agent_text_message_count += len(identity_replies)
        output_texts.extend(
            str(message_value(message, "content") or "") for message in identity_replies
        )
        identity_reply = identity_replies[0]
        assert_content_contains([identity_reply], agent_name)
        assert_content_contains([identity_reply], chat_id)
        assert_content_contains([identity_reply], user_name)
        echo_absent_before_invite = echo_id not in await participant_ids(
            api_client, chat_id
        )
        assert echo_absent_before_invite
        identity_content = str(message_value(identity_reply, "content") or "")
        current_room_line = _labeled_line(identity_content, "CURRENT_ROOM")
        invitable_peers_line = _labeled_line(identity_content, "INVITABLE_PEERS")
        assert user_name.lower() in current_room_line.lower(), identity_content
        assert agent_name.lower() in current_room_line.lower(), identity_content
        assert all(
            not value or value.lower() not in current_room_line.lower()
            for value in (echo_name, echo_handle)
        ), identity_content
        assert any(
            value and value.lower() in invitable_peers_line.lower()
            for value in (echo_name, echo_handle)
        ), identity_content
        assert user_name.lower() not in invitable_peers_line.lower(), identity_content
        assert agent_name.lower() not in invitable_peers_line.lower(), identity_content
        step_1_tool_observations = await _wait_for_required_tool_observations_or_block(
            agent_observation_client,
            room_id=chat_id,
            agent_id=agent_id,
            after_message_id=step_1_trigger_id,
            required_tool_names={
                "band_get_participants",
                "band_lookup_peers",
            },
            timeout=_STEP_TIMEOUT,
            adapter_name=adapter_name,
        )

        before_step_2 = message_ids(await fetch_chat_messages(api_client, chat_id))
        step_2_prompt = (
            "invite Echo to this chat and send them the exact message HELLO_ECHO"
        )
        input_texts.append(step_2_prompt)
        step_2_trigger_id = await send_trigger_message(
            api_client,
            chat_id,
            step_2_prompt,
            agent_name,
            agent_id,
        )
        step_2_messages = await wait_for_chat_messages(
            api_client,
            chat_id,
            lambda messages: any(
                str(message_value(message, "content") or "").strip() == "HELLO_ECHO"
                and mention_ids(message) == {echo_id}
                for message in agent_text_messages(messages, agent_id, before_step_2)
            ),
            _STEP_TIMEOUT,
        )
        await wait_until_participant_present(
            api_client, chat_id, echo_id, _STEP_TIMEOUT
        )
        agent_hello_messages = [
            message
            for message in agent_text_messages(step_2_messages, agent_id, before_step_2)
            if str(message_value(message, "content") or "").strip() == "HELLO_ECHO"
            and mention_ids(message) == {echo_id}
        ]
        assert len(agent_hello_messages) == 1, [
            message_value(message, "content")
            for message in agent_text_messages(step_2_messages, agent_id, before_step_2)
        ]
        observed_agent_text_message_count += len(agent_hello_messages)
        output_texts.extend(
            str(message_value(message, "content") or "")
            for message in agent_hello_messages
        )
        hello_message = agent_hello_messages[0]
        step_2_tool_observations = await _wait_for_required_tool_observations_or_block(
            agent_observation_client,
            room_id=chat_id,
            agent_id=agent_id,
            after_message_id=step_2_trigger_id,
            required_tool_names={
                "band_add_participant",
                "band_send_message",
            },
            timeout=_STEP_TIMEOUT,
            adapter_name=adapter_name,
        )

        echo_received = await wait_for_chat_messages(
            api_client,
            chat_id,
            lambda messages: any(
                message_value(message, "sender_id") == echo_id
                and message_value(message, "message_type") == "text"
                and str(message_value(message, "id")) not in before_step_2
                and "ECHO: HELLO_ECHO" in str(message_value(message, "content") or "")
                for message in messages
            ),
            _STEP_TIMEOUT,
        )
        echo_message = next(
            message
            for message in echo_received
            if message_value(message, "sender_id") == echo_id
            and message_value(message, "message_type") == "text"
            and str(message_value(message, "id")) not in before_step_2
            and "ECHO: HELLO_ECHO" in str(message_value(message, "content") or "")
        )
        post_echo_agent_messages = await _agent_messages_after(
            api_client,
            chat_id,
            agent_id,
            after_message=echo_message,
            duration=_LOOP_SUPPRESSION_WINDOW,
        )
        assert len(post_echo_agent_messages) <= 1, [
            message_value(message, "content") for message in post_echo_agent_messages
        ]
        if post_echo_agent_messages:
            post_echo = post_echo_agent_messages[0]
            post_echo_content = str(message_value(post_echo, "content") or "").strip()
            assert post_echo_content
            assert (
                post_echo_content
                != str(message_value(hello_message, "content") or "").strip()
            )
            assert post_echo_content != "ECHO: HELLO_ECHO"
            assert "HELLO_ECHO" not in post_echo_content
            assert echo_id not in mention_ids(post_echo)

        step_3_prompt = "remove Echo from this chat"
        input_texts.append(step_3_prompt)
        step_3_trigger_id = await send_trigger_message(
            api_client,
            chat_id,
            step_3_prompt,
            agent_name,
            agent_id,
        )
        await wait_until_participant_absent(
            api_client,
            chat_id,
            echo_id,
            _STEP_TIMEOUT,
        )
        step_3_tool_observations = await _wait_for_required_tool_observations_or_block(
            agent_observation_client,
            room_id=chat_id,
            agent_id=agent_id,
            after_message_id=step_3_trigger_id,
            required_tool_names={"band_remove_participant"},
            timeout=_STEP_TIMEOUT,
            adapter_name=adapter_name,
        )
        echo_removed = echo_id not in await participant_ids(api_client, chat_id)
        all_tool_observations = [
            *step_1_tool_observations,
            *step_2_tool_observations,
            *step_3_tool_observations,
        ]
        write_baseline_tier2_artifact(
            scenario_id="L0.request.platform_context",
            scenario_refs=_L0_SCENARIO_REFS,
            adapter=adapter_name,
            timer=timer,
            pricing=pricing,
            provider_usage=l0_usage_from_live_observation(
                adapter=adapter,
                adapter_name=adapter_name,
                input_texts=input_texts,
                output_texts=output_texts,
                observed_agent_text_message_count=observed_agent_text_message_count,
            ),
            input_texts=input_texts,
            output_texts=output_texts,
            observed_agent_text_message_count=observed_agent_text_message_count,
            evidence={
                "L0.request.platform_context": {
                    "identity_reply_count": len(identity_replies),
                    "identity_reply_message_id": str(
                        message_value(identity_reply, "id")
                    ),
                    "agent_name_observed": agent_name,
                    "room_id_observed": chat_id,
                    "user_name_observed": user_name,
                },
                "L0.request.history": {
                    "input_turn_count": len(input_texts),
                    "output_turn_count": len(output_texts),
                    "step_1_trigger_id": step_1_trigger_id,
                    "step_2_trigger_id": step_2_trigger_id,
                    "step_3_trigger_id": step_3_trigger_id,
                },
                "L0.request.participants": {
                    "current_room_excludes_echo": echo_absent_before_invite,
                    "current_room_line_checked": True,
                    "invitable_peers_line_checked": True,
                },
                "L0.dispatch.lookup_peers": {
                    "echo_invitable_in_identity_reply": True,
                    "echo_name": echo_name,
                    "echo_handle": echo_handle,
                    "observed_tool_events": [
                        {
                            "id": observation.event_id,
                            "message_type": observation.message_type,
                            "tool_call_id": observation.tool_call_id,
                        }
                        for observation in step_1_tool_observations
                        if observation.tool_name == "band_lookup_peers"
                    ],
                },
                "L0.dispatch.add_participant": {
                    "echo_present_after_invite": True,
                    "echo_id": echo_id,
                    "observed_tool_events": [
                        {
                            "id": observation.event_id,
                            "message_type": observation.message_type,
                            "tool_call_id": observation.tool_call_id,
                        }
                        for observation in step_2_tool_observations
                        if observation.tool_name == "band_add_participant"
                    ],
                },
                "L0.dispatch.send_message": {
                    "hello_message_count": len(agent_hello_messages),
                    "hello_message_id": str(message_value(hello_message, "id")),
                    "hello_mention_ids": sorted(mention_ids(hello_message)),
                    "observed_tool_events": [
                        {
                            "id": observation.event_id,
                            "message_type": observation.message_type,
                            "tool_call_id": observation.tool_call_id,
                        }
                        for observation in step_2_tool_observations
                        if observation.tool_name == "band_send_message"
                    ],
                },
                "L0.dispatch.get_participants": {
                    "identity_roster_reply_count": len(identity_replies),
                    "echo_roster_lookup_replied": str(
                        message_value(echo_message, "id")
                    ),
                    "observed_tool_events": [
                        {
                            "id": observation.event_id,
                            "message_type": observation.message_type,
                            "tool_call_id": observation.tool_call_id,
                        }
                        for observation in step_1_tool_observations
                        if observation.tool_name == "band_get_participants"
                    ],
                },
                "L0.dispatch.remove_participant": {
                    "echo_removed": echo_removed,
                    "post_echo_agent_message_count": len(post_echo_agent_messages),
                    "observed_tool_events": [
                        {
                            "id": observation.event_id,
                            "message_type": observation.message_type,
                            "tool_call_id": observation.tool_call_id,
                        }
                        for observation in step_3_tool_observations
                    ],
                },
                "observed_tool_events": [
                    {
                        "id": observation.event_id,
                        "tool_name": observation.tool_name,
                        "message_type": observation.message_type,
                        "tool_call_id": observation.tool_call_id,
                    }
                    for observation in all_tool_observations
                ],
            },
            platform_observations=[
                {
                    "kind": "message",
                    "id": str(message_value(identity_reply, "id")),
                    "assertion": "identity/context answer observed",
                    "scenario_refs": [
                        "L0.request.platform_context",
                        "L0.request.participants",
                        "L0.dispatch.get_participants",
                        "L0.dispatch.lookup_peers",
                    ],
                },
                {
                    "kind": "participant",
                    "id": echo_id,
                    "assertion": "Echo participant present after invite request",
                    "scenario_ref": "L0.dispatch.add_participant",
                },
                {
                    "kind": "message",
                    "id": str(message_value(hello_message, "id")),
                    "assertion": "HELLO_ECHO sent with exact Echo mention metadata",
                    "scenario_ref": "L0.dispatch.send_message",
                },
                {
                    "kind": "room_state",
                    "id": chat_id,
                    "assertion": "Echo participant removed after remove request",
                    "scenario_ref": "L0.dispatch.remove_participant",
                },
                *_tool_observation_records(
                    step_1_tool_observations,
                    "Step 1 used live read tools for participants and peers",
                ),
                *_tool_observation_records(
                    step_2_tool_observations,
                    "Step 2 used live add-participant and send-message tools",
                ),
                *_tool_observation_records(
                    step_3_tool_observations,
                    "Step 4 used live remove-participant tool",
                ),
            ],
        )
