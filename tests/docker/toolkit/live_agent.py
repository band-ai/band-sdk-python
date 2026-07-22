"""Provision + run a real containerized echo agent against a live platform.

Bundles what a live container-based test needs to get to the actual
scenario: a provisioned agent identity, a room, and that agent's echo
process running inside a real container as the non-root ``agent`` user.
Lives here (not in docker_cli.py) because it's band/agent-specific, not
generic Docker plumbing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from tests.docker.toolkit.docker_cli import Container, Image
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager

SDK_PYTHON = "$BAND_SDK_PYTHON"

# Real, standalone, ruff-checked script (tests/docker/echo_agent.py) — read as
# text and shipped into the container via `$BAND_SDK_PYTHON -c <source>`, so a
# refactor there shows up here instead of silently drifting until this runs.
ECHO_AGENT_SCRIPT = (Path(__file__).parents[1] / "echo_agent.py").read_text()


@asynccontextmanager
async def live_containerized_echo_agent(
    image: Image,
    resource_manager: ResourceManager,
    baseline_settings: BaselineSettings,
    *,
    label: str,
) -> AsyncIterator[tuple[ProvisionedAgent, str]]:
    """Provision an agent + room, run its echo process in a real container,
    and yield ``(agent, room_id)`` for the caller to drive a turn against.

    Yields right after launching the echo process in the background
    (``docker exec -d`` returns as soon as it's launched, not once it's
    actually connected) without waiting for an explicit readiness signal.
    That's deliberate, not an oversight: a bare Phoenix channel subscribe
    does NOT replay a message sent before it joins (verified directly), but
    ``Agent.start()`` doesn't just subscribe — it fetches room state via
    REST as part of its own startup, which picks up a message sent to a
    room the agent is already a participant of (true here: provision_room
    adds it before the container even starts) regardless of live-WS
    subscribe timing. Verified empirically: an injected 5s delay before
    Agent.create() was even called (~25x the ~0.4s `import band` cost
    measured on this machine) still got a reply. If this fixture is ever
    reused for a scenario that ISN'T the room's first message (i.e. not
    is_session_bootstrap), re-verify this holds — the bootstrap/hydrate
    path is what's actually relied on here, not general message delivery.
    """
    agent = await resource_manager.provision_agent(label)
    room_id = await resource_manager.provision_room(participants=[agent.id])

    with Container.run(
        image,
        name_prefix="band-python-kit-live-test",
        env={
            "BAND_AGENT_ID": agent.id,
            "BAND_API_KEY": agent.api_key,
            "BAND_WS_URL": baseline_settings.endpoints.ws_url,
            "BAND_REST_URL": baseline_settings.endpoints.rest_url,
        },
    ) as container:
        container.run_python_background(
            ECHO_AGENT_SCRIPT, interpreter=SDK_PYTHON, user="agent"
        )
        yield agent, room_id
