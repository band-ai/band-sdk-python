"""Gated live L3 multi-participant scenario.

Run with:
    E2E_TESTS_ENABLED=true E2E_BASELINE_L3_LIVE=true \
        E2E_L3_TEST_AGENT_ID=<uuid> E2E_L3_TEST_AGENT_API_KEY=<agent-key> E2E_L3_TEST_AGENT_NAME=<name> E2E_L3_TEST_AGENT_HANDLE=<handle> E2E_L3_TEST_AGENT_DESCRIPTION=<description> \
        E2E_L3_CALC_AGENT_ID=<uuid> E2E_L3_CALC_AGENT_API_KEY=<agent-key> E2E_L3_CALC_AGENT_NAME=<name> E2E_L3_CALC_AGENT_HANDLE=<handle> E2E_L3_CALC_AGENT_DESCRIPTION=<description> \
        E2E_L3_GREETER_AGENT_ID=<uuid> E2E_L3_GREETER_AGENT_API_KEY=<agent-key> E2E_L3_GREETER_AGENT_NAME=<name> E2E_L3_GREETER_AGENT_HANDLE=<handle> E2E_L3_GREETER_AGENT_DESCRIPTION=<description> \
        uv run pytest tests/e2e/scenarios/test_baseline_l3_multiparty.py -v -s --no-cov
"""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

import pytest
from thenvoi_rest import AsyncRestClient, ChatRoomRequest
from thenvoi_rest.types import ParticipantRequest

from thenvoi.agent import Agent
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.runtime.types import AgentConfig

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
    write_provider_usage_blocked_artifact_if_needed,
)
from tests.e2e.conftest import (
    E2ESettings,
    _assert_room_creation_budget_available,
    _track_created_room,
    requires_e2e,
)
from tests.e2e.helpers import (
    agent_text_messages,
    fetch_chat_messages,
    mention_ids,
    message_ids,
    message_value,
    send_trigger_message,
)

_STEP_TIMEOUT = 90.0
_L3_SCENARIO_REFS = [
    "L3.request.roster_handles",
    "L3.request.mention_convention",
    "L3.request.multi_author_history",
]


@dataclass(frozen=True)
class _LiveAgentSpec:
    role: str
    agent_id: str
    api_key: str
    name: str
    handle: str
    description: str


_REQUIRED_L3_ENV = (
    "E2E_L3_TEST_AGENT_ID",
    "E2E_L3_TEST_AGENT_API_KEY",
    "E2E_L3_TEST_AGENT_NAME",
    "E2E_L3_TEST_AGENT_HANDLE",
    "E2E_L3_TEST_AGENT_DESCRIPTION",
    "E2E_L3_CALC_AGENT_ID",
    "E2E_L3_CALC_AGENT_API_KEY",
    "E2E_L3_CALC_AGENT_NAME",
    "E2E_L3_CALC_AGENT_HANDLE",
    "E2E_L3_CALC_AGENT_DESCRIPTION",
    "E2E_L3_GREETER_AGENT_ID",
    "E2E_L3_GREETER_AGENT_API_KEY",
    "E2E_L3_GREETER_AGENT_NAME",
    "E2E_L3_GREETER_AGENT_HANDLE",
    "E2E_L3_GREETER_AGENT_DESCRIPTION",
)


def _l3_live_blocked_reason() -> str | None:
    if os.environ.get("E2E_BASELINE_L3_LIVE") != "true":
        return "tier2_blocked: E2E_BASELINE_L3_LIVE=true not set for live L3 flow"
    missing = [name for name in _REQUIRED_L3_ENV if not os.environ.get(name)]
    if missing:
        return f"tier2_blocked: missing live L3 configuration {', '.join(missing)}"
    return None


def _specs() -> tuple[_LiveAgentSpec, _LiveAgentSpec, _LiveAgentSpec]:
    test_name = os.environ["E2E_L3_TEST_AGENT_NAME"]
    calc_name = os.environ["E2E_L3_CALC_AGENT_NAME"]
    greeter_name = os.environ["E2E_L3_GREETER_AGENT_NAME"]
    calc_handle = os.environ["E2E_L3_CALC_AGENT_HANDLE"].lstrip("@")
    greeter_handle = os.environ["E2E_L3_GREETER_AGENT_HANDLE"].lstrip("@")
    test_handle = os.environ["E2E_L3_TEST_AGENT_HANDLE"].lstrip("@")
    return (
        _LiveAgentSpec(
            role="test",
            agent_id=os.environ["E2E_L3_TEST_AGENT_ID"],
            api_key=os.environ["E2E_L3_TEST_AGENT_API_KEY"],
            name=test_name,
            handle=test_handle,
            description=os.environ["E2E_L3_TEST_AGENT_DESCRIPTION"],
        ),
        _LiveAgentSpec(
            role="calc",
            agent_id=os.environ["E2E_L3_CALC_AGENT_ID"],
            api_key=os.environ["E2E_L3_CALC_AGENT_API_KEY"],
            name=calc_name,
            handle=calc_handle,
            description=os.environ["E2E_L3_CALC_AGENT_DESCRIPTION"],
        ),
        _LiveAgentSpec(
            role="greeter",
            agent_id=os.environ["E2E_L3_GREETER_AGENT_ID"],
            api_key=os.environ["E2E_L3_GREETER_AGENT_API_KEY"],
            name=greeter_name,
            handle=greeter_handle,
            description=os.environ["E2E_L3_GREETER_AGENT_DESCRIPTION"],
        ),
    )


async def _create_l3_room(
    client: AsyncRestClient,
    test_spec: _LiveAgentSpec,
    calc_spec: _LiveAgentSpec,
    greeter_spec: _LiveAgentSpec,
    created_room_ids: list[str],
    room_creation_budget: int,
    user_peer: Any,
) -> str:
    _assert_room_creation_budget_available(
        created_room_ids=created_room_ids,
        budget=room_creation_budget,
        label="baseline L3 multiparty room",
    )
    chat = await client.agent_api_chats.create_agent_chat(chat=ChatRoomRequest())
    room_id = chat.data.id
    _track_created_room(
        created_room_ids=created_room_ids,
        budget=room_creation_budget,
        room_id=room_id,
        label="baseline L3 multiparty room",
    )
    for participant_id in (
        user_peer.id,
        test_spec.agent_id,
        calc_spec.agent_id,
        greeter_spec.agent_id,
    ):
        try:
            await client.agent_api_participants.add_agent_chat_participant(
                room_id,
                participant=ParticipantRequest(
                    participant_id=participant_id, role="member"
                ),
            )
        except Exception as exc:
            if getattr(exc, "status_code", None) != 409:
                raise
    return room_id


def _content(message: Any) -> str:
    return str(message_value(message, "content") or "")


def _visible_handle(spec: _LiveAgentSpec) -> str:
    return f"@{spec.handle.lstrip('@')}"


def _assert_visible_handle(messages: list[Any], spec: _LiveAgentSpec) -> None:
    visible_handle = _visible_handle(spec).lower()
    assert messages
    assert all(visible_handle in _content(message).lower() for message in messages), [
        _content(message) for message in messages
    ]


async def _assert_participant_descriptions(
    client: AsyncRestClient,
    room_id: str,
    specs: tuple[_LiveAgentSpec, ...],
) -> None:
    response = await client.agent_api_participants.list_agent_chat_participants(room_id)
    by_id = {participant.id: participant for participant in (response.data or [])}
    for spec in specs:
        participant = by_id.get(spec.agent_id)
        assert participant is not None, f"missing participant {spec.agent_id}"
        description = str(getattr(participant, "description", "") or "")
        assert spec.description.lower() in description.lower(), {
            "agent_id": spec.agent_id,
            "expected_description": spec.description,
            "observed_description": description,
        }


def _new_agent_messages(
    messages: list[Any],
    sender_id: str,
    before_ids: set[str],
) -> list[Any]:
    return agent_text_messages(messages, sender_id, before_ids)


def _messages_mentioning(
    messages: list[Any],
    sender_id: str,
    target_id: str,
    before_ids: set[str],
) -> list[Any]:
    return [
        message
        for message in _new_agent_messages(messages, sender_id, before_ids)
        if target_id in mention_ids(message)
    ]


def _messages_containing(
    messages: list[Any],
    sender_id: str,
    text: str,
    before_ids: set[str],
) -> list[Any]:
    return [
        message
        for message in _new_agent_messages(messages, sender_id, before_ids)
        if text.lower() in _content(message).lower()
    ]


def _message_position(messages: list[Any], target: Any) -> int:
    target_id = str(message_value(target, "id"))
    return next(
        index
        for index, message in enumerate(messages)
        if str(message_value(message, "id")) == target_id
    )


async def _wait_full_turn_window(
    client: AsyncRestClient,
    room_id: str,
) -> list[Any]:
    deadline = asyncio.get_running_loop().time() + _STEP_TIMEOUT
    while True:
        await fetch_chat_messages(client, room_id)
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(0.5, remaining))
    return await fetch_chat_messages(client, room_id)


_L3_LIVE_BLOCKED_REASON = _l3_live_blocked_reason()
pytestmark = pytest.mark.skipif(
    _L3_LIVE_BLOCKED_REASON is not None,
    reason=_L3_LIVE_BLOCKED_REASON or "tier2_blocked: unknown L3 live block",
)


@pytest.fixture(
    params=tuple(BASELINE_DEFAULT_PROVIDER_USAGE_ADAPTER_FACTORIES.items()),
    ids=lambda item: item[0],
)
def l3_provider_usage_adapter_entry(
    request: pytest.FixtureRequest,
) -> tuple[str, AdapterFactory]:
    adapter_name, factory = request.param
    return str(adapter_name), factory


@pytest.mark.parametrize(
    "adapter_name", BASELINE_DEFAULT_PROVIDER_USAGE_BLOCKED_ADAPTER_NAMES
)
def test_l3_live_unsupported_adapter_rows_write_blocked_artifacts_when_configured(
    adapter_name: str,
) -> None:
    blocked_reason = _l3_live_blocked_reason()
    if blocked_reason:
        pytest.skip(blocked_reason)
    blocked_reason = write_provider_usage_blocked_artifact_if_needed(
        scenario_id="L3.request.roster_handles",
        scenario_refs=_L3_SCENARIO_REFS,
        adapter=adapter_name,
    )
    assert blocked_reason is not None


@pytest.mark.asyncio
@pytest.mark.timeout(600)
@requires_e2e
async def test_l3_live_three_independent_real_adapter_instances_when_configured(
    e2e_config: E2ESettings,
    l3_provider_usage_adapter_entry: tuple[str, AdapterFactory],
    api_client: AsyncRestClient,
    e2e_session_client: AsyncRestClient,
    e2e_created_room_ids: list[str],
    e2e_room_creation_budget: int,
    e2e_user_peer: Any,
) -> None:
    blocked_reason = _l3_live_blocked_reason()
    if blocked_reason:
        pytest.skip(blocked_reason)

    timer = start_baseline_tier2_timer()
    pricing = baseline_pricing_from_env()
    input_texts: list[str] = []
    output_texts: list[str] = []
    test_spec, calc_spec, greeter_spec = _specs()
    specs = (test_spec, calc_spec, greeter_spec)
    adapter_name, adapter_factory = l3_provider_usage_adapter_entry
    agent_ids = {spec.agent_id for spec in specs}
    assert len(agent_ids) == 3

    adapters: list[SimpleAdapter[Any]] = []
    async with AsyncExitStack() as stack:
        for spec in specs:
            adapter = adapter_factory(e2e_config)
            adapter.clear_provider_usage()
            adapters.append(adapter)
            agent = Agent.create(
                adapter=adapter,
                agent_id=spec.agent_id,
                api_key=spec.api_key,
                ws_url=e2e_config.thenvoi_ws_url,
                rest_url=e2e_config.thenvoi_base_url,
                config=AgentConfig(auto_subscribe_existing_rooms=False),
            )
            await stack.enter_async_context(agent)

        room_id = await _create_l3_room(
            e2e_session_client,
            test_spec,
            calc_spec,
            greeter_spec,
            e2e_created_room_ids,
            e2e_room_creation_budget,
            e2e_user_peer,
        )
        await _assert_participant_descriptions(e2e_session_client, room_id, specs)

        before_t1 = message_ids(await fetch_chat_messages(api_client, room_id))
        t1_prompt = f"ask {calc_spec.name} what is 7 times 8"
        input_texts.append(t1_prompt)
        await send_trigger_message(
            api_client,
            room_id,
            t1_prompt,
            test_spec.name,
            test_spec.agent_id,
        )
        t1_messages = await _wait_full_turn_window(api_client, room_id)
        t1_test_to_calc = _messages_mentioning(
            t1_messages, test_spec.agent_id, calc_spec.agent_id, before_t1
        )
        t1_calc = _messages_containing(t1_messages, calc_spec.agent_id, "56", before_t1)
        t1_test_relays = [
            message
            for message in _messages_containing(
                t1_messages, test_spec.agent_id, "56", before_t1
            )
            if calc_spec.agent_id not in mention_ids(message)
        ]
        assert len(t1_test_to_calc) == 1, [
            _content(message) for message in t1_test_to_calc
        ]
        _assert_visible_handle(t1_test_to_calc, calc_spec)
        assert len(t1_calc) == 1, [_content(message) for message in t1_messages]
        assert len(t1_test_relays) == 1, [
            _content(message) for message in t1_test_relays
        ]
        assert not _new_agent_messages(t1_messages, greeter_spec.agent_id, before_t1)
        output_texts.extend(
            _content(message)
            for participant_id in agent_ids
            for message in _new_agent_messages(t1_messages, participant_id, before_t1)
        )

        before_t2 = message_ids(await fetch_chat_messages(api_client, room_id))
        t2_prompt = (
            "I need a personalized greeting for someone named ORACLE. "
            "Ask whoever is best suited in this room."
        )
        input_texts.append(t2_prompt)
        await send_trigger_message(
            api_client,
            room_id,
            t2_prompt,
            test_spec.name,
            test_spec.agent_id,
        )
        t2_messages = await _wait_full_turn_window(api_client, room_id)
        t2_test_to_greeter = _messages_mentioning(
            t2_messages, test_spec.agent_id, greeter_spec.agent_id, before_t2
        )
        t2_test_to_calc = _messages_mentioning(
            t2_messages, test_spec.agent_id, calc_spec.agent_id, before_t2
        )
        t2_test_relays = [
            message
            for message in _messages_containing(
                t2_messages, test_spec.agent_id, "ORACLE", before_t2
            )
            if greeter_spec.agent_id not in mention_ids(message)
        ]
        t2_greeter_replies = _messages_containing(
            t2_messages, greeter_spec.agent_id, "ORACLE", before_t2
        )
        assert len(t2_test_to_greeter) == 1, [
            _content(message) for message in t2_test_to_greeter
        ]
        _assert_visible_handle(t2_test_to_greeter, greeter_spec)
        assert not t2_test_to_calc, [_content(message) for message in t2_test_to_calc]
        assert len(t2_greeter_replies) == 1, [
            _content(message) for message in t2_messages
        ]
        assert len(t2_test_relays) == 1, [
            _content(message) for message in t2_test_relays
        ]
        assert not _new_agent_messages(t2_messages, calc_spec.agent_id, before_t2)
        output_texts.extend(
            _content(message)
            for participant_id in agent_ids
            for message in _new_agent_messages(t2_messages, participant_id, before_t2)
        )

        before_t3 = message_ids(await fetch_chat_messages(api_client, room_id))
        t3_prompt = (
            f"ask {calc_spec.name} what is 12 times 5, and instruct them to ask "
            f"{greeter_spec.name} to write a greeting for someone turning that age "
            "and send that to me"
        )
        input_texts.append(t3_prompt)
        await send_trigger_message(
            api_client,
            room_id,
            t3_prompt,
            test_spec.name,
            test_spec.agent_id,
        )
        t3_messages = await _wait_full_turn_window(api_client, room_id)
        t3_test_to_calc = _messages_mentioning(
            t3_messages, test_spec.agent_id, calc_spec.agent_id, before_t3
        )
        t3_calc_to_greeter = _messages_mentioning(
            t3_messages, calc_spec.agent_id, greeter_spec.agent_id, before_t3
        )
        t3_test_relays = [
            message
            for message in _messages_containing(
                t3_messages, test_spec.agent_id, "60", before_t3
            )
            if calc_spec.agent_id not in mention_ids(message)
        ]
        assert len(t3_test_to_calc) == 1, [
            _content(message) for message in t3_test_to_calc
        ]
        _assert_visible_handle(t3_test_to_calc, calc_spec)
        assert len(t3_calc_to_greeter) == 1, [
            _content(message) for message in t3_calc_to_greeter
        ]
        _assert_visible_handle(t3_calc_to_greeter, greeter_spec)
        assert "60" in _content(t3_calc_to_greeter[0])
        t3_greeter_replies = _messages_containing(
            t3_messages, greeter_spec.agent_id, "60", before_t3
        )
        assert len(t3_greeter_replies) == 1, [
            _content(message) for message in t3_greeter_replies
        ]
        assert len(t3_test_relays) == 1, [_content(message) for message in t3_messages]
        # REST messages are newest first, so the earlier Test -> Calc request
        # should appear later in the list than Calc's follow-on Greeter request.
        assert _message_position(t3_messages, t3_test_to_calc[0]) > _message_position(
            t3_messages,
            t3_calc_to_greeter[0],
        )
        output_texts.extend(
            _content(message)
            for participant_id in agent_ids
            for message in _new_agent_messages(t3_messages, participant_id, before_t3)
        )

        before_t4 = message_ids(await fetch_chat_messages(api_client, room_id))
        t4_prompt = (
            "ask for help calculating what 25% older than that birthday age would be, "
            "and add a jest about being a quarter older to the card"
        )
        input_texts.append(t4_prompt)
        await send_trigger_message(
            api_client,
            room_id,
            t4_prompt,
            greeter_spec.name,
            greeter_spec.agent_id,
        )
        t4_messages = await _wait_full_turn_window(api_client, room_id)
        t4_greeter_to_calc = _messages_mentioning(
            t4_messages, greeter_spec.agent_id, calc_spec.agent_id, before_t4
        )
        assert len(t4_greeter_to_calc) == 1, [
            _content(message) for message in t4_messages
        ]
        _assert_visible_handle(t4_greeter_to_calc, calc_spec)
        assert "60" in _content(t4_greeter_to_calc[0])
        t4_calc_replies = _messages_containing(
            t4_messages, calc_spec.agent_id, "75", before_t4
        )
        t4_greeter_replies = [
            message
            for message in _messages_containing(
                t4_messages, greeter_spec.agent_id, "75", before_t4
            )
            if calc_spec.agent_id not in mention_ids(message)
        ]
        assert len(t4_calc_replies) == 1, [
            _content(message) for message in t4_calc_replies
        ]
        assert len(t4_greeter_replies) == 1, [
            _content(message) for message in t4_greeter_replies
        ]
        assert _message_position(t4_messages, t4_calc_replies[0]) < _message_position(
            t4_messages, t4_greeter_replies[0]
        )
        assert not _new_agent_messages(t4_messages, test_spec.agent_id, before_t4)
        output_texts.extend(
            _content(message)
            for participant_id in agent_ids
            for message in _new_agent_messages(t4_messages, participant_id, before_t4)
        )
        write_baseline_tier2_artifact(
            scenario_id="L3.request.roster_handles",
            scenario_refs=_L3_SCENARIO_REFS,
            adapter=adapter_name,
            timer=timer,
            pricing=pricing,
            provider_usage=aggregate_provider_usage(
                [
                    snapshot
                    for adapter in adapters
                    for snapshot in adapter.provider_usage_snapshots()
                ]
            ),
            input_texts=input_texts,
            output_texts=output_texts,
            observed_agent_text_message_count=len(output_texts),
            evidence={
                "L3.request.roster_handles": {
                    "participant_descriptions_observed": True,
                    "visible_handles_asserted": True,
                },
                "L3.request.mention_convention": {
                    "turn_1_test_to_calc": len(t1_test_to_calc),
                    "turn_2_test_to_greeter": len(t2_test_to_greeter),
                    "turn_3_calc_to_greeter": len(t3_calc_to_greeter),
                    "turn_4_greeter_to_calc": len(t4_greeter_to_calc),
                },
                "L3.request.multi_author_history": {
                    "turn_1_calc_reply_count": len(t1_calc),
                    "turn_2_greeter_reply_count": len(t2_greeter_replies),
                    "turn_3_test_to_calc": len(t3_test_to_calc),
                    "turn_4_test_silence": not _new_agent_messages(
                        t4_messages, test_spec.agent_id, before_t4
                    ),
                },
            },
            platform_observations=[
                {
                    "kind": "message",
                    "id": str(message_value(t1_test_to_calc[0], "id")),
                    "assertion": "Turn 1 routed exactly once from Test to Calc",
                    "scenario_refs": [
                        "L3.request.roster_handles",
                        "L3.request.mention_convention",
                    ],
                },
                {
                    "kind": "message",
                    "id": str(message_value(t2_test_to_greeter[0], "id")),
                    "assertion": "Turn 2 routed exactly once from Test to Greeter",
                    "scenario_ref": "L3.request.mention_convention",
                },
                {
                    "kind": "message",
                    "id": str(message_value(t3_calc_to_greeter[0], "id")),
                    "assertion": "Turn 3 Calc delegated exactly once to Greeter",
                    "scenario_refs": [
                        "L3.request.mention_convention",
                        "L3.request.multi_author_history",
                    ],
                },
                {
                    "kind": "message",
                    "id": str(message_value(t4_greeter_to_calc[0], "id")),
                    "assertion": "Turn 4 Greeter delegated exactly once to Calc",
                    "scenario_refs": [
                        "L3.request.mention_convention",
                        "L3.request.multi_author_history",
                    ],
                },
            ],
        )
