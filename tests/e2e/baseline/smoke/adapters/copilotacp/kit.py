"""Shared Copilot ACP smoke helpers: hermetic configs, reply turns, and
fresh-process reads of Copilot's own session record."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from band.converters.acp_client import ACPClientHistoryConverter
from band.core.model_catalog import ModelSelection
from band.core.types import metadata_to_dict
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import liveness_probe
from tests.e2e.baseline.toolkit.builders import copilot_acp_env, copilot_home_dir
from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.observations.events import Tasks
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent
from tests.e2e.baseline.toolkit.user_ops import UserOps


def skip_without_github_token(settings: BaselineSettings) -> None:
    """Hosted-auth coverage is optional; the BYOK cells are the lane's bar."""
    if not settings.backends.github_token:
        pytest.skip("GITHUB_TOKEN unset — the Copilot-hosted smokes need one")


def hermetic_copilot_config(
    settings: BaselineSettings,
    work_dir: Path,
    *,
    hosted: bool = False,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> Any:
    """A per-test ``CopilotACPAdapterConfig`` with a fresh cwd + ``COPILOT_HOME``.

    The fresh home makes each config hermetic (host extensions and session
    state cannot steer the turn) and makes a later config's ACP
    ``session/load`` deterministically miss. ``hosted=False`` (default)
    mirrors the registry builder's Anthropic BYOK env (``copilot_acp_env``);
    ``hosted=True`` omits it and authenticates with ``github_token`` — the
    Copilot-hosted path production users run. ``model`` / ``reasoning_effort``
    set the typed selection the adapter applies from each session's
    advertised catalog; a hosted ``model`` defaults to
    ``settings.backends.copilot_hosted_model``, since the env's
    ``COPILOT_MODEL`` does not pick an ACP session's model.
    """
    from band.adapters.copilot_acp import (  # noqa: PLC0415 -- copilot_acp imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        CopilotACPAdapterConfig,
    )

    home = copilot_home_dir(str(work_dir))
    if hosted and model is None:
        model = settings.backends.copilot_hosted_model
    kwargs: dict[str, Any] = {
        "cwd": str(work_dir),
        "custom_section": "Keep responses short and concise.",
        "env": ({"COPILOT_HOME": home} if hosted else copilot_acp_env(settings, home)),
        "model": model,
        "reasoning_effort": reasoning_effort,
    }
    if hosted:
        kwargs["github_token"] = settings.backends.github_token
    if settings.backends.copilot_command.strip():
        kwargs["command"] = tuple(settings.backends.copilot_command.split())
    return CopilotACPAdapterConfig(**kwargs)


async def reply_with(
    capture: ReplyCapture,
    *,
    marker: str,
    user_ops: UserOps,
    identity: ProvisionedAgent,
    settings: BaselineSettings,
) -> None:
    """One room turn whose reply must carry ``marker``."""
    mark = capture.messages.snapshot()
    mid = await user_ops.send_message(
        capture.room_id,
        liveness_probe(marker),
        mention_id=identity.id,
        mention_name=identity.name,
    )
    replies = await capture.wait_for_reply(
        mid, identity.id, since=mark, deadline_s=settings.e2e_timeout
    )
    replies.assert_contains_any([marker])


def room_session_id(tasks: Tasks, room_id: str) -> str:
    """The ACP session the adapter last reported for ``room_id``, read the way
    it reads one back to restore it."""
    reported = (
        ACPClientHistoryConverter()
        .convert([{"metadata": metadata_to_dict(task.metadata)} for task in tasks])
        .room_to_session
    )
    assert room_id in reported, f"no ACP session reported for room {room_id}"
    return reported[room_id]


async def copilot_persisted_selection(
    config: Any, *, room_id: str, session_id: str
) -> ModelSelection:
    """What a fresh ``copilot --acp`` reports ``session_id`` runs after
    ``session/load``: Copilot's own record, not the adapter's. It is spawned
    by a fresh adapter's own runtime wiring (launcher, env, workspace)."""
    from band.adapters.copilot_acp import (  # noqa: PLC0415 -- the acp extra, as above
        CopilotACPAdapter,
    )
    from band.integrations.acp.model_selection import (  # noqa: PLC0415 -- the acp extra, as above
        locate_model_options,
    )
    from band.integrations.acp.session_config import (  # noqa: PLC0415 -- the acp extra, as above
        session_config_options,
    )

    spawner = CopilotACPAdapter(config)
    room = spawner._room(room_id)
    runtime = spawner._build_runtime(room, room.generation)
    await runtime.start()
    try:
        loaded = await runtime.load_session_response(
            cwd=room.workspace, session_id=session_id, mcp_servers=[]
        )
    finally:
        await runtime.stop()
    assert loaded is not None, f"Copilot could not load session {session_id}"
    return locate_model_options(
        session_config_options(loaded) or ()
    ).current_selection()
