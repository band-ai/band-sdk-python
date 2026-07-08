"""Copilot SDK showcase smokes — the toolkit driving the Copilot adapter live.

Copilot SDK is a ``core``-lane matrix adapter (gated on the Anthropic BYOK key;
its Copilot auth — a stored login or ``GITHUB_TOKEN`` — is out-of-band, see the
builder in ``toolkit/builders.py``), so the generic matrix (``smoke/matrix/``)
already runs the standard scenarios against it via the registry builder. These
are Copilot-focused instead: ``ask_user`` routing (handler and room mode), recall
when Copilot's *native* session resume misses, and one client shared across adapter
lifecycles — none of which the generic builder's ``prompt``/``features``/``tools``
contract can express, so each test constructs ``CopilotSDKAdapter`` by hand (like
``test_parlant.py``) and hands it to the toolkit's run primitives.

Because construction is bespoke, these expose no adapter binding to the lane
selector (no ``@with_adapters``/``@per_adapter`` — that would demand the ``agent``
fixture and re-provision generically). So gating stays explicit (``@requires``) and
lane scoping is pinned with ``@lane(Lane.CORE)`` — without it the selector would see
no framework and run these heavy smokes in *every* lane.

Run with:
    E2E_TESTS_ENABLED=true BAND_E2E_LANE=core uv run pytest \\
        tests/e2e/baseline/smoke/adapters/test_copilot_sdk.py -v -s --no-cov
"""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

import pytest

from band.adapters.copilot_sdk import ASK_USER_ROOM, _COPILOT_SDK_AVAILABLE

from tests.e2e.baseline.flaky import flaky_infra

from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_agent,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

pytestmark = pytest.mark.skipif(
    not _COPILOT_SDK_AVAILABLE,
    reason="github-copilot-sdk not installed (pip install band-sdk[copilot_sdk])",
)


def _copilot_config(settings: BaselineSettings, **overrides: Any) -> Any:
    """The showcase's base ``CopilotSDKAdapterConfig`` (BYOK on Anthropic).

    Mirrors the registry builder's shape (``toolkit/builders.py``) so these
    bespoke tests don't re-derive it; ``overrides`` layers the one knob each
    test actually cares about (``ask_user=``, ``base_directory=``).
    """
    from copilot import ProviderConfig

    from band.adapters.copilot_sdk import CopilotSDKAdapterConfig

    return CopilotSDKAdapterConfig(
        model=settings.llm_models.anthropic_model,
        provider=ProviderConfig(
            type="anthropic",
            base_url="https://api.anthropic.com",
            api_key=settings.llm_credentials.anthropic_api_key,
        ),
        github_token=settings.backends.github_token,
        custom_section=overrides.pop(
            "custom_section", "Keep responses short and concise."
        ),
        **overrides,
    )


def _chosen_word(reply: str) -> str:
    """Extract the word the agent chose from its phase-1 reply.

    The prompt asks for exactly one word; tolerate mentions/punctuation by
    taking the last alphabetic token of meaningful length.
    """
    words = re.findall(r"[A-Za-z]{4,}", reply)
    assert words, f"phase-1 reply contains no candidate word: {reply!r}"
    return words[-1]


@lane(Lane.CORE)  # bespoke build exposes no framework; pin scheduling to core
@requires(Dep.ANTHROPIC)
@flaky_infra(
    "Copilot runtime boot + ask_user double round-trip can time out transiently"
)
# ask_user adds a second model round trip (ask -> handler answers -> final
# reply) on top of Copilot's own runtime boot.
@pytest.mark.timeout(extra=120)
@pytest.mark.asyncio(loop_scope="session")
async def test_copilot_ask_user_handler_round_trips_to_room_reply(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """``ask_user=<handler>`` pauses the turn, a programmatic operator answers.

    The operator's answer is a high-entropy token the user never utters, so the
    reply containing it proves the answer flowed through the handler — not
    model invention. Guards the adapter's ``ask_user`` allowlisting and handler
    forwarding (without either, the model cannot ask and the handler never
    fires).
    """
    from band.adapters.copilot_sdk import CopilotSDKAdapter

    operator_channel = f"channel-{uuid.uuid4().hex[:6]}"
    asked: list[dict[str, Any]] = []

    async def fake_operator(
        request: dict[str, Any], context: dict[str, str]
    ) -> dict[str, Any]:
        asked.append(dict(request))
        return {"answer": operator_channel, "wasFreeform": True}

    adapter = CopilotSDKAdapter(
        _copilot_config(
            baseline_settings,
            ask_user=fake_operator,
            custom_section=(
                "Keep responses short and concise. A human operator is "
                "available through the ask_user tool for decisions you "
                "cannot make alone."
            ),
        )
    )

    async with running_provisioned_agent(
        adapter, resource_manager, label="copilot-ask-user"
    ) as agent:
        room_id = await resource_manager.provision_room(
            title="e2e-copilot-ask-user", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                "I need to deploy release v2. Use the ask_user tool to ask "
                "your operator which channel to deploy to and wait for my "
                "response before continuing. Then reply stating exactly "
                "the channel the operator chose.",
                mention_id=agent.id,
                mention_name=agent.name,
            )
            replies = await capture.wait_for_reply(
                mid, agent.id, deadline_s=baseline_settings.e2e_timeout * 2
            )

    assert asked, "the model never called ask_user (handler did not fire)"
    assert asked[0].get("question"), f"ask_user carried no question: {asked[0]!r}"
    # Only the operator handler can supply this token; the reply containing it
    # proves the answer round-tripped through the turn.
    replies.assert_contains_any([operator_channel])


@lane(Lane.CORE)  # bespoke build exposes no framework; pin scheduling to core
@requires(Dep.ANTHROPIC)
@flaky_infra("two full live turns plus Copilot runtime boot can time out transiently")
@pytest.mark.timeout(extra=180)  # two full turns (question turn + answer turn)
@pytest.mark.asyncio(loop_scope="session")
async def test_copilot_ask_user_room_question_answered_by_next_message(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """``ask_user="room"`` posts the question into the room and ends the turn;
    the user's next room message answers it on the same persisted session.

    The answer is a high-entropy token never uttered before the answer, so a
    reply containing it proves the answer flowed through the room round trip —
    not model invention.
    """
    from band.adapters.copilot_sdk import CopilotSDKAdapter

    secret_channel = f"channel-{uuid.uuid4().hex[:6]}"
    adapter = CopilotSDKAdapter(
        _copilot_config(
            baseline_settings,
            ask_user=ASK_USER_ROOM,
            custom_section=(
                "Keep responses short and concise. Never invent answers to "
                "questions you asked — wait for the user."
            ),
        )
    )

    async with running_provisioned_agent(
        adapter, resource_manager, label="copilot-ask-user-room"
    ) as agent:
        room_id = await resource_manager.provision_room(
            title="e2e-copilot-ask-user-room", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            # Turn 1: the trigger makes the model call ask_user; the question
            # must land in the room, not anywhere else.
            mark = capture.messages.snapshot()
            mid = await user_ops.send_message(
                room_id,
                "Use the ask_user tool to ask me which channel to deploy "
                "release v2 to. After I answer, reply stating exactly the "
                "channel I chose.",
                mention_id=agent.id,
                mention_name=agent.name,
            )
            replies = await capture.wait_for_reply(
                mid, agent.id, since=mark, deadline_s=baseline_settings.e2e_timeout * 2
            )
            replies.assert_contains_any(["channel"])

            # Turn 2: the user's next room message is the answer.
            mark = capture.messages.snapshot()
            mid = await user_ops.send_message(
                room_id,
                f"Deploy to the {secret_channel} channel.",
                mention_id=agent.id,
                mention_name=agent.name,
            )
            replies = await capture.wait_for_reply(
                mid, agent.id, since=mark, deadline_s=baseline_settings.e2e_timeout * 2
            )
            # Only the answer message contains this token; the reply relaying
            # it proves the question/answer round-tripped through the room.
            replies.assert_contains_any([secret_channel])


@lane(Lane.CORE)  # bespoke build exposes no framework; pin scheduling to core
@requires(Dep.ANTHROPIC)
@flaky_infra(
    "two fresh Copilot runtime boots (resume-miss setup) can time out transiently"
)
# Two agent lifecycles, each booting a fresh Copilot runtime into an empty
# base_directory (the resume-miss setup) — the heaviest boot path in this file.
@pytest.mark.timeout(extra=180)
@pytest.mark.asyncio(loop_scope="session")
async def test_copilot_recall_via_injected_history_when_resume_misses(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    tmp_path: Any,
) -> None:
    """Recall must survive a restart that invalidates Copilot's *native* resume.

    A rejoin against the same on-disk state (a plain stop/restart) would let
    Copilot's own session resume answer for free. This test gives each phase a
    fresh ``base_directory``, so phase 2's resume finds no state and recall
    must flow through the converter's injected text history instead. Two facts
    are asserted after the restart: a code the USER stated (plain injected-
    history recall), and a word the AGENT itself chose in phase 1 — the user
    never utters it, so the agent's own injected replies are its only possible
    source (the regression case for one-sided injected history).
    """
    from band.adapters.copilot_sdk import CopilotSDKAdapter

    secret_code = f"CODE_{uuid.uuid4().hex[:6]}"

    def make_adapter(phase: str) -> CopilotSDKAdapter:
        # A fresh base_directory per phase: phase 2 has no on-disk session
        # state, so Copilot's native resume cannot help.
        return CopilotSDKAdapter(
            _copilot_config(baseline_settings, base_directory=str(tmp_path / phase))
        )

    identity = await resource_manager.provision_agent("copilot-resume-miss")
    room_id = await resource_manager.provision_room(
        title="e2e-copilot-resume-miss", participants=[identity.id]
    )

    # Phase 1: seed a user fact and make the agent produce its own fact.
    async with running_agent(identity, make_adapter("phase1"), baseline_settings):
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                f"Remember this secret code: {secret_code}. Now choose one "
                "uncommon English word yourself and reply with exactly that "
                "single word, nothing else.",
                mention_id=identity.id,
                mention_name=identity.name,
            )
            replies = await capture.wait_for_reply(
                mid, identity.id, deadline_s=baseline_settings.e2e_timeout
            )
            agent_word = _chosen_word(replies[-1].content)

    # Phase 2: fresh process state AND fresh Copilot state directory — recall
    # can only come from the platform history the adapter injects.
    async with running_agent(identity, make_adapter("phase2"), baseline_settings):
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                "What was the secret code I told you, and what was the "
                "single word you chose earlier? Reply with both.",
                mention_id=identity.id,
                mention_name=identity.name,
            )
            replies = await capture.wait_for_reply(
                mid, identity.id, deadline_s=baseline_settings.e2e_timeout
            )
            replies.assert_contains_any([secret_code])
            # The user never uttered this word — only the agent's own injected
            # phase-1 reply can supply it.
            replies.assert_contains_any([agent_word])


@lane(Lane.CORE)  # bespoke build exposes no framework; pin scheduling to core
@requires(Dep.ANTHROPIC)
@flaky_infra(
    "several live turns across two adapter lifecycles can time out transiently"
)
# Two sequential adapter lifecycles on one shared runtime, the first serving
# two concurrent room sessions — several live turns, so widen the outer budget.
@pytest.mark.timeout(extra=180)
@pytest.mark.asyncio(loop_scope="session")
async def test_copilot_shared_client_across_adapter_lifecycles(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Proves the Copilot SDK's one-client/many-sessions model through the
    adapter against the live platform:

    1. one ``CopilotClient`` is created by the test (the owner);
    2. an adapter *borrows* it and serves TWO rooms with turns in flight
       CONCURRENTLY — two isolated parallel sessions on one runtime;
    3. the first adapter shuts down and a successor adapter reuses the same
       still-running client — the borrowed client must survive an adapter's
       full cleanup (``owns_client=False`` contract).
    """
    from copilot import CopilotClient

    from band.adapters.copilot_sdk import CopilotSDKAdapter

    identity = await resource_manager.provision_agent("copilot-shared-client")
    room_a = await resource_manager.provision_room(
        title="e2e-copilot-shared-a", participants=[identity.id]
    )
    room_b = await resource_manager.provision_room(
        title="e2e-copilot-shared-b", participants=[identity.id]
    )

    async def smoke(room_id: str) -> None:
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                "Please say hello.",
                mention_id=identity.id,
                mention_name=identity.name,
            )
            replies = await capture.wait_for_reply(
                mid, identity.id, deadline_s=baseline_settings.e2e_timeout
            )
        replies.assert_present(what="a copilot_sdk[shared-client] reply")

    def make_shared_adapter(client: Any) -> CopilotSDKAdapter:
        return CopilotSDKAdapter(_copilot_config(baseline_settings), client=client)

    # The test owns the client; adapters only borrow it.
    client = CopilotClient(github_token=baseline_settings.backends.github_token)
    try:
        async with running_agent(
            identity, make_shared_adapter(client), baseline_settings
        ):
            # Two rooms with turns in flight AT THE SAME TIME — parallel
            # sessions on one runtime, serialized only per room.
            await asyncio.gather(smoke(room_a), smoke(room_b))

        # First adapter fully cleaned up; the borrowed client must have
        # survived — a successor adapter reuses it without a restart.
        async with running_agent(
            identity, make_shared_adapter(client), baseline_settings
        ):
            await smoke(room_a)
    finally:
        await client.stop()
