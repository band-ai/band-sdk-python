"""Declarative offline baseline scenarios for the first injection path."""

from __future__ import annotations

import pytest

from band.core.types import AdapterFeatures, Capability, Emit

from tests.baseline.decisions import ModelDecision
from tests.baseline.harness import BaselineScenario
from tests.baseline.platform import BaselineTools


@pytest.mark.asyncio
async def test_message_round_trip_uses_the_adapter_tool_exposure_path() -> None:
    scenario = BaselineScenario(
        [
            ModelDecision.call(
                "band_send_message",
                content="Hello @baseline-user",
                mentions=["@baseline-user"],
            ),
            ModelDecision.text_reply("delivered"),
        ]
    )

    observation = await scenario.run("Say hello")

    observation.assert_tool_called(
        "band_send_message", content="Hello @baseline-user", mentions=["@baseline-user"]
    )
    scenario.tools.assert_message_sent(content="Hello @baseline-user")
    assert scenario.tools.schema_requests == [
        {"format": "anthropic", "include_memory": False, "include_contacts": False}
    ]
    scenario.assert_complete()


@pytest.mark.asyncio
async def test_execution_events_follow_a_platform_tool_call() -> None:
    scenario = BaselineScenario(
        [
            ModelDecision.call(
                "band_send_event", content="working", message_type="thought"
            ),
            ModelDecision.text_reply("done"),
        ],
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )

    observation = await scenario.run("Show your work")

    observation.assert_tool_called(
        "band_send_event", content="working", message_type="thought"
    )
    observation.assert_event("tool_call", "band_send_event")
    observation.assert_event("tool_result", "band_send_event")
    observation.assert_event("thought", "working")
    scenario.assert_complete()


@pytest.mark.asyncio
async def test_shared_memory_is_observable_through_the_platform_tool_surface() -> None:
    scenario = BaselineScenario(
        [
            ModelDecision.call(
                "band_store_memory",
                content="Baseline preference",
                system="long_term",
                type="semantic",
                segment="user",
                thought="remember it",
                scope="subject",
            ),
            ModelDecision.text_reply("stored"),
        ],
        features=AdapterFeatures(capabilities={Capability.MEMORY}),
    )

    observation = await scenario.run("Remember this")

    observation.assert_tool_called("band_store_memory", content="Baseline preference")
    assert [memory["content"] for memory in scenario.tools.memories] == [
        "Baseline preference"
    ]
    assert scenario.tools.schema_requests[0]["include_memory"] is True
    scenario.assert_complete()


@pytest.mark.asyncio
async def test_malformed_tool_arguments_are_reported_without_platform_io() -> None:
    scenario = BaselineScenario(
        [
            ModelDecision.call("band_send_message", mentions=["@baseline-user"]),
            ModelDecision.text_reply("recovered"),
        ],
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )

    observation = await scenario.run("Send a malformed call")

    observation.assert_tool_called("band_send_message")
    observation.assert_event("tool_result", "Error:")
    scenario.tools.assert_no_messages_sent()
    scenario.assert_complete()


@pytest.mark.asyncio
async def test_room_histories_are_isolated() -> None:
    scenario = BaselineScenario(
        [ModelDecision.text_reply("one"), ModelDecision.text_reply("two")]
    )

    await scenario.run("first", room_id="room-one", message_id="message-one")
    await scenario.run("second", room_id="room-two", message_id="message-two")
    scenario.assert_complete()

    assert [
        entry["content"] for entry in scenario.adapter._message_history["room-one"]
    ] == [
        "[Baseline User]: first",
        "one",
    ]
    assert [
        entry["content"] for entry in scenario.adapter._message_history["room-two"]
    ] == [
        "[Baseline User]: second",
        "two",
    ]


@pytest.mark.asyncio
async def test_permanent_model_failure_surfaces_an_error_event() -> None:
    scenario = BaselineScenario([RuntimeError("model unavailable")])

    with pytest.raises(RuntimeError, match="model unavailable"):
        await scenario.run("Try a model call")

    assert any(event["message_type"] == "error" for event in scenario.tools.events_sent)
    scenario.assert_complete()


@pytest.mark.asyncio
async def test_contact_tool_round_trip_is_local_and_observable() -> None:
    tools = BaselineTools()
    scenario = BaselineScenario(
        [
            ModelDecision.call(
                "band_respond_contact_request", action="approve", request_id="request-1"
            ),
            ModelDecision.text_reply("approved"),
        ],
        features=AdapterFeatures(capabilities={Capability.CONTACTS}),
        tools=tools,
    )

    observation = await scenario.run("Approve the request")

    observation.assert_tool_called("band_respond_contact_request", action="approve")
    assert tools.contact_requests[0]["result"]["status"] == "approved"
    scenario.assert_complete()


@pytest.mark.asyncio
async def test_history_rehydration_and_contact_broadcast_reach_the_adapter() -> None:
    scenario = BaselineScenario([ModelDecision.text_reply("recalled")])
    history = [
        {"role": "user", "content": "[Baseline User]: previously saved marker-alpha"},
        {"role": "assistant", "content": "I will remember marker-alpha"},
    ]

    await scenario.run(
        "What was the marker?",
        history=history,
        contacts_msg="[Contacts]: @alice is now a contact",
    )

    contents = [
        entry["content"] for entry in scenario.adapter._message_history["room-baseline"]
    ]
    assert contents == [
        "[Baseline User]: previously saved marker-alpha",
        "I will remember marker-alpha",
        "[System]: [Contacts]: @alice is now a contact",
        "[Baseline User]: What was the marker?",
        "recalled",
    ]
    scenario.assert_complete()


@pytest.mark.asyncio
async def test_retry_after_a_transient_model_failure_uses_the_next_decision() -> None:
    scenario = BaselineScenario(
        [
            RuntimeError("temporary provider failure"),
            ModelDecision.text_reply("recovered"),
        ]
    )

    with pytest.raises(RuntimeError, match="temporary provider failure"):
        await scenario.run("Retry this", message_id="message-first")
    await scenario.run("Retry this", message_id="message-retry")

    assert [
        entry["content"] for entry in scenario.adapter._message_history["room-baseline"]
    ] == [
        "[Baseline User]: Retry this",
        "recovered",
    ]
    assert (
        len(
            [
                event
                for event in scenario.tools.events_sent
                if event["message_type"] == "error"
            ]
        )
        == 1
    )
    scenario.assert_complete()


@pytest.mark.asyncio
async def test_task_events_are_dispatched_through_the_same_platform_surface() -> None:
    scenario = BaselineScenario(
        [
            ModelDecision.call(
                "band_send_event",
                content="task complete",
                message_type="task",
                metadata={"status": "completed"},
            ),
            ModelDecision.text_reply("done"),
        ]
    )

    observation = await scenario.run("Record completion")

    observation.assert_tool_called("band_send_event", message_type="task")
    observation.assert_event("task", "task complete")
    scenario.assert_complete()
