"""Live smoke coverage for the outbound ACP room-visible tool contract.

The scenario is intentionally backend-neutral: it asks the ACP agent to emit one
Band event, then checks that the event was persisted and that the call was
narrated as an ordinary ACP tool call — like any other tool, with no special
suppression for Band messaging tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from band.core.types import MessageType

from tests.e2e.baseline.agents import Adapter, Lane, lane, with_adapters
from tests.e2e.baseline.flaky import flaky_infra, flaky_model
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import (
    TOOL_AGENT,
    emit_event_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    ProvisionedAgent,
    ResourceManager,
    running_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps


@with_adapters(Adapter.COPILOT_ACP, **TOOL_AGENT)
@flaky_model("the ACP agent may occasionally miss the explicit tool-only request")
@pytest.mark.timeout(extra=180)
@pytest.mark.asyncio(loop_scope="session")
async def test_acp_band_tool_call_is_narrated(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A band_send_event call is narrated as an ACP tool_call, like any other tool.

    Uses the raw ``events`` reader (not the JSON-based ``tool_calls`` helper):
    ACP narrates a tool_call's content as the plain ACP-reported title (e.g.
    ``"band_send_event"``), not the ``{"name": ..., "args": ...}`` JSON shape
    other adapters use, so a substring check is the right tool here.
    """
    marker = unique_marker("acp-event")
    room_id = await resource_manager.provision_room(
        title="e2e-acp-tool-call-narrated", participants=[agent.id]
    )

    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            emit_event_instruction(MessageType.THOUGHT, marker),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)
        thoughts = await capture.thoughts(sender_id=agent.id)
        tool_call_events = await capture.events(
            MessageType.TOOL_CALL, sender_id=agent.id
        )

    thoughts.assert_contains_any([marker])
    tool_call_events.assert_at_least(1)
    tool_call_events.assert_contains_any(["band_send_event"])


@with_adapters(Adapter.COPILOT_ACP, **TOOL_AGENT)
@flaky_model("the ACP agent may occasionally miss the explicit tool-only request")
@pytest.mark.timeout(extra=180)
@pytest.mark.asyncio(loop_scope="session")
async def test_acp_band_tool_result_is_a_single_clean_payload(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A Band tool's tool_result event carries the tool's output exactly once.

    An MCP bridge that forwards both a result's readable text and its
    structuredContent companion into one block duplicates the payload -- the
    room event then reads as the same JSON twice (once readable, once
    re-encoded). The contract: the emitted tool_result content is a single
    well-formed JSON document, the platform's actual response.

    The marker proves the tool ran (via the thought it posted); it is NOT
    asserted inside the tool_result, because the platform's create-event
    response (``{id, message_type, success}``) does not echo the content. The
    JSON check is scoped to the Band tool's results (selected by the response's
    ``"success"`` field): Copilot also narrates its own internal tools (e.g.
    skill loading), whose results are legitimately plain text.
    """
    marker = unique_marker("acp-result")
    room_id = await resource_manager.provision_room(
        title="e2e-acp-tool-result-clean", participants=[agent.id]
    )

    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            emit_event_instruction(MessageType.THOUGHT, marker),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)
        thoughts = await capture.thoughts(sender_id=agent.id)
        tool_results = await capture.events(MessageType.TOOL_RESULT, sender_id=agent.id)

    thoughts.assert_contains_any([marker])
    band_results = tool_results.containing('"success"')
    band_results.assert_at_least(1)
    band_results.assert_json_content()


def resumemiss_config(settings: BaselineSettings, phase_dir: Path) -> Any:
    """A per-phase ``CopilotACPAdapterConfig`` whose Copilot state cannot survive.

    Mirrors the registry builder's shape (``toolkit/builders.py``) with one twist:
    ``COPILOT_HOME`` is *always* a fresh per-phase directory (the builder gates that
    isolation on a configured token), so a later phase's ACP ``session/load``
    deterministically misses and the room-history replay fallback is the only
    possible source of context. With ambient (non-token) auth Copilot must hold its
    credential outside ``~/.copilot`` (e.g. the OS keychain) for this to run.
    """
    from band.adapters.copilot_acp import CopilotACPAdapterConfig

    home = phase_dir / "copilot-home"
    home.mkdir(parents=True)
    kwargs: dict[str, Any] = {
        "cwd": str(phase_dir),
        "env": {"COPILOT_HOME": str(home)},
        "github_token": settings.backends.github_token or None,
        "custom_section": "Keep responses short and concise.",
    }
    if settings.backends.copilot_command.strip():
        kwargs["command"] = tuple(settings.backends.copilot_command.split())
    return CopilotACPAdapterConfig(**kwargs)


@lane(Lane.BACKENDS)  # bespoke build exposes no framework; pin scheduling to backends
@requires(Dep.COPILOT_CLI)
@flaky_infra(
    "two fresh Copilot CLI boots (session-load-miss setup) can time out transiently"
)
# Two agent lifecycles, each booting a fresh Copilot CLI with an empty
# COPILOT_HOME (the session-load-miss setup) — the heaviest boot path here.
@pytest.mark.timeout(extra=300)
@pytest.mark.asyncio(loop_scope="session")
async def test_acp_recall_via_room_replay_when_session_load_misses(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    tmp_path: Any,
) -> None:
    """Recall must survive a restart that invalidates ACP's native session resume.

    A plain stop/restart against surviving Copilot state would let ACP
    ``session/load`` answer for free, so a green recall would not prove the
    fallback. This test gives each phase a fresh ``COPILOT_HOME``: phase 2's
    ``session/load`` finds no state and recall can only flow through the Band
    room transcript the adapter replays into the new session's first prompt.
    Two facts are asserted after the restart: a tracking marker the USER stated
    (plain replay recall), and a calibration answer the AGENT produced in
    phase 1 — the user never utters that answer, so the agent's own replayed
    reply lines are its only possible source (the regression case for a replay
    that drops the agent's side of the transcript).
    """
    from band.adapters.copilot_acp import CopilotACPAdapter

    tracking_marker = unique_marker("acp-replay")
    agent_fact = "blue"

    def make_adapter(phase: str) -> CopilotACPAdapter:
        return CopilotACPAdapter(resumemiss_config(baseline_settings, tmp_path / phase))

    identity = await resource_manager.provision_agent("acp-session-load-miss")
    room_id = await resource_manager.provision_room(
        title="e2e-acp-session-load-miss", participants=[identity.id]
    )

    # Phase 1: seed a user fact and make the agent produce its own fact.
    async with running_agent(identity, make_adapter("phase1"), baseline_settings):
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                "Create a short project log note for later reference. The "
                f"tracking marker is {tracking_marker}. Also answer this "
                "calibration question inside your reply: what color is a "
                "clear daytime sky? Reply in one short sentence that includes "
                "the tracking marker and the color answer.",
                mention_id=identity.id,
                mention_name=identity.name,
            )
            replies = await capture.wait_for_reply(
                mid, identity.id, deadline_s=baseline_settings.e2e_timeout
            )
            replies.assert_contains_any([tracking_marker])
            # Must be in the transcript now, or phase 2 has nothing to replay.
            replies.assert_contains_any([agent_fact])

    # Phase 2: fresh process AND fresh COPILOT_HOME — session/load misses, so
    # recall can only come from the replayed Band room transcript.
    async with running_agent(identity, make_adapter("phase2"), baseline_settings):
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                "From the earlier project log, what was the tracking marker "
                "and what color answer did you give? Reply with both.",
                mention_id=identity.id,
                mention_name=identity.name,
            )
            replies = await capture.wait_for_reply(
                mid, identity.id, deadline_s=baseline_settings.e2e_timeout
            )
            replies.assert_contains_any([tracking_marker])
            # The user never uttered this answer — only the agent's own
            # replayed phase-1 reply can supply it.
            replies.assert_contains_any([agent_fact])
