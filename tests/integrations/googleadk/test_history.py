"""Native ADK requests retain inbound history across failed and retried turns."""

from __future__ import annotations

from dataclasses import replace

import pytest
from google.genai import types
from google.genai.errors import ServerError

from band.adapters.google_adk import GoogleADKAdapter, GoogleADKAdapterConfig
from band.core.exceptions import ProviderRunError, ToolRoundsExceededError
from band.runtime.formatters import format_current_inputs
from band.testing import FakeAgentTools
from tests.adapters.genaikit import no_candidates, text_reply
from tests.framework_conformance.turnprobes import turn_input, user_message
from tests.integrations.googleadk.kit import (
    DeclineInput,
    GoogleModelPeer,
    WorkInput,
    batch,
    decline,
    reject,
    request_text,
)


class TestHistory:
    async def test_failed_inputs_stay_bounded_and_recover_with_recent_context(
        self, google_model: GoogleModelPeer
    ) -> None:
        first = user_message("FAILED-ASK-0")
        failed_inputs = [
            replace(first, id=f"failed-{index}", content=f"FAILED-ASK-{index}")
            for index in range(5)
        ]
        recovery = replace(first, id="recovery", content="RECOVERY-ASK")
        adapter = GoogleADKAdapter(
            GoogleADKAdapterConfig(max_history_messages=2),
            additional_tools=[(DeclineInput, decline)],
        )
        await adapter.on_started("Agent", "An agent")
        tools = FakeAgentTools()
        google_model.responses.extend(no_candidates() for _ in failed_inputs)

        for message in failed_inputs:
            with pytest.raises(ProviderRunError):
                await adapter.on_event(
                    replace(turn_input(tools, message), is_session_bootstrap=False)
                )

        assert adapter._room_history == {
            first.room_id: [
                {
                    "id": message.id,
                    "role": "user",
                    "content": message.format_for_llm(),
                }
                for message in failed_inputs[-2:]
            ]
        }

        google_model.responses.append(batch(("decline", {})))
        await adapter.on_event(
            replace(turn_input(tools, recovery), is_session_bootstrap=False)
        )

        assert request_text(google_model.requests[-1]) == (
            "[Previous conversation context]\n"
            f"{failed_inputs[-2].format_for_llm()}\n"
            f"{failed_inputs[-1].format_for_llm()}\n"
            "[End of previous context]\n\n\n"
            f"{format_current_inputs([recovery])}"
        )

    @pytest.mark.parametrize(
        ("responses", "error"),
        [
            (
                [ServerError(503, {"error": {"message": "Provider unavailable"}})],
                ServerError,
            ),
            ([no_candidates()], ProviderRunError),
            ([batch(("work", {}))], ToolRoundsExceededError),
        ],
        ids=["provider-exception", "model-error-event", "native-call-and-round-cap"],
    )
    @pytest.mark.parametrize("bootstrap", [True, False], ids=["bootstrap", "unseeded"])
    async def test_failed_input_reaches_next_request_once_in_order(
        self,
        google_model: GoogleModelPeer,
        responses: list[types.GenerateContentResponse | Exception],
        error: type[Exception],
        bootstrap: bool,
    ) -> None:
        first = user_message("FIRST-ASK-MARKER")
        second = replace(first, id="second", content="SECOND-ASK-MARKER")
        tools = FakeAgentTools()
        adapter = GoogleADKAdapter(
            GoogleADKAdapterConfig(max_tool_rounds=1),
            additional_tools=[(DeclineInput, decline), (WorkInput, reject)],
        )
        await adapter.on_started("Agent", "An agent")
        google_model.responses.extend(responses)

        with pytest.raises(error):
            await adapter.on_event(
                replace(turn_input(tools, first), is_session_bootstrap=bootstrap)
            )
        google_model.responses.append(batch(("decline", {})))
        await adapter.on_event(
            replace(turn_input(tools, second), is_session_bootstrap=False)
        )

        assert google_model.requests[-1] == [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=(
                            "[Previous conversation context]\n"
                            f"{first.format_for_llm()}\n"
                            "[End of previous context]\n\n\n"
                            f"{format_current_inputs([second])}"
                        )
                    )
                ],
            )
        ]

    async def test_retry_does_not_duplicate_current_input_or_remember_it_twice(
        self, google_model: GoogleModelPeer
    ) -> None:
        first = user_message("RETRIED-ASK-MARKER")
        second = replace(first, id="second", content="FOLLOWING-ASK-MARKER")
        tools = FakeAgentTools()
        adapter = GoogleADKAdapter(additional_tools=[(DeclineInput, decline)])
        await adapter.on_started("Agent", "An agent")
        google_model.responses.extend(
            [
                no_candidates(),
                no_candidates(),
                batch(("decline", {})),
                batch(("decline", {})),
            ]
        )

        with pytest.raises(ProviderRunError):
            await adapter.on_event(turn_input(tools, first))
        with pytest.raises(ProviderRunError):
            await adapter.on_event(
                replace(turn_input(tools, first), is_session_bootstrap=False)
            )
        await adapter.on_event(
            replace(turn_input(tools, first), is_session_bootstrap=False)
        )
        await adapter.on_event(
            replace(turn_input(tools, second), is_session_bootstrap=False)
        )

        assert [
            request_text(request).count(first.content)
            for request in google_model.requests
        ] == [1, 1, 1, 1]
        text = request_text(google_model.requests[-1])
        assert text.index(first.content) < text.index(second.content)

    async def test_failed_partial_model_text_is_not_a_completed_reply(
        self, google_model: GoogleModelPeer
    ) -> None:
        first = user_message("FIRST-ASK-MARKER")
        second = replace(first, id="second", content="SECOND-ASK-MARKER")
        unfinished_answer = "UNFINISHED-ANSWER"
        partial = batch(("work", {}))
        partial.candidates[0].content.parts.insert(
            0, types.Part.from_text(text=unfinished_answer)
        )
        google_model.responses.extend(
            [partial, no_candidates(), batch(("decline", {}))]
        )
        adapter = GoogleADKAdapter(
            additional_tools=[(WorkInput, reject), (DeclineInput, decline)]
        )
        await adapter.on_started("Agent", "An agent")
        tools = FakeAgentTools()

        with pytest.raises(ProviderRunError):
            await adapter.on_event(turn_input(tools, first))
        await adapter.on_event(
            replace(turn_input(tools, second), is_session_bootstrap=False)
        )

        text = request_text(google_model.requests[-1])
        assert text.count(first.content) == 1
        assert unfinished_answer not in text

    async def test_successful_batch_and_reply_stay_ordered_and_room_local(
        self, google_model: GoogleModelPeer
    ) -> None:
        first = user_message("FIRST-ASK-MARKER")
        # Identical text from a different message is a distinct delivery.
        repeated = replace(first, id="repeated")
        second = replace(first, id="second", content="SECOND-ASK-MARKER")
        other = replace(
            first, id="other", room_id="other-room", content="OTHER-ROOM-MARKER"
        )
        completed_answer = "COMPLETED-ANSWER"
        google_model.responses.extend(
            [
                text_reply(completed_answer),
                text_reply("OTHER-ANSWER"),
                text_reply("DONE"),
            ]
        )
        adapter = GoogleADKAdapter()
        await adapter.on_started("Agent", "An agent")

        await adapter.on_turn(
            [first, repeated],
            FakeAgentTools(),
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id=first.room_id,
        )
        await adapter.on_turn(
            [other],
            FakeAgentTools(),
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id=other.room_id,
        )
        await adapter.on_turn(
            [second],
            FakeAgentTools(),
            [],
            None,
            None,
            is_session_bootstrap=False,
            room_id=first.room_id,
        )

        assert request_text(google_model.requests[1]) == format_current_inputs([other])
        assert request_text(google_model.requests[-1]) == (
            "[Previous conversation context]\n"
            f"{first.format_for_llm()}\n{repeated.format_for_llm()}\n"
            f"[Agent]: {completed_answer}\n[End of previous context]\n\n\n"
            f"{format_current_inputs([second])}"
        )

    async def test_failed_batch_is_room_local_and_cleanup_releases_its_history(
        self, google_model: GoogleModelPeer
    ) -> None:
        first = user_message("FIRST-ASK-MARKER")
        earlier = replace(first, id="earlier", content="EARLIER-ASK-MARKER")
        companion = replace(first, id="companion", content="BATCH-ASK-MARKER")
        second = replace(first, id="second", content="SECOND-ASK-MARKER")
        # The same message ID in another room must not share deduplication.
        other = replace(first, room_id="other-room", content="OTHER-ROOM-MARKER")
        google_model.responses.extend(
            [
                batch(("decline", {})),
                no_candidates(),
                batch(("decline", {})),
                batch(("decline", {})),
                batch(("decline", {})),
            ]
        )
        adapter = GoogleADKAdapter(additional_tools=[(DeclineInput, decline)])
        await adapter.on_started("Agent", "An agent")
        tools = FakeAgentTools()

        await adapter.on_event(turn_input(tools, earlier))
        with pytest.raises(ProviderRunError):
            await adapter.on_messages(
                replace(
                    turn_input(tools, first),
                    messages=(first, companion),
                    is_session_bootstrap=False,
                )
            )
        await adapter.on_event(turn_input(FakeAgentTools(), other))
        await adapter.on_event(
            replace(turn_input(tools, second), is_session_bootstrap=False)
        )
        await adapter.on_cleanup(first.room_id)
        await adapter.on_event(
            replace(turn_input(tools, second), is_session_bootstrap=False)
        )

        assert request_text(google_model.requests[2]) == format_current_inputs([other])
        assert request_text(google_model.requests[3]) == (
            "[Previous conversation context]\n"
            f"{earlier.format_for_llm()}\n"
            f"{first.format_for_llm()}\n{companion.format_for_llm()}\n"
            "[End of previous context]\n\n\n"
            f"{format_current_inputs([second])}"
        )
        assert request_text(google_model.requests[4]) == format_current_inputs([second])
