"""Shared Copilot ACP smoke helpers: hermetic configs, reply turns, and
fresh-process reads of Copilot's own session record."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from band.converters.acp_client import ACPClientHistoryConverter
from band.core.model_catalog import ModelSelection
from band.core.validation import available
from band.runtime.context_serialization import context_item_to_dict
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import liveness_probe
from tests.e2e.baseline.toolkit.builders import (
    copilot_acp_env,
    copilot_cli_env,
    copilot_home_dir,
)
from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.observations.events import Tasks
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent
from tests.e2e.baseline.toolkit.user_ops import UserOps

# The room id a catalog probe's session runs under; it never meets the platform.
CATALOG_PROBE_ROOM = "catalog-probe"


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
    advertised catalog; ``None`` leaves Copilot's own default, which for a
    billed hosted turn can be an expensive model, so those callers pass
    ``settings.backends.copilot_hosted_model``. Every spawn keeps the pinned
    CLI version (``copilot_cli_env``).
    """
    from band.adapters.copilot_acp import (  # noqa: PLC0415 -- copilot_acp imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        CopilotACPAdapterConfig,
    )

    home = copilot_home_dir(str(work_dir))
    kwargs: dict[str, Any] = {
        "cwd": str(work_dir),
        "custom_section": "Keep responses short and concise.",
        "env": copilot_cli_env(home) if hosted else copilot_acp_env(settings, home),
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
    """One settled room turn whose reply must carry ``marker``.

    The reply leaves mid-turn through a Band tool, while what the adapter
    records at the turn's end (its ACP session) lands after it, so the turn is
    also awaited to ``PROCESSED`` before the next step reads or changes it.
    """
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
    await capture.wait_for_processed(mid, identity.id, deadline_s=settings.e2e_timeout)


def room_session_id(tasks: Tasks, room_id: str) -> str:
    """The ACP session the adapter last reported for ``room_id``, read the way
    it reads one back to restore it: whole history messages, since a record
    is recognized by its type and label as well as its metadata."""
    reported = (
        ACPClientHistoryConverter()
        .convert([context_item_to_dict(task) for task in tasks])
        .room_to_session
    )
    assert room_id in reported, f"no ACP session reported for room {room_id}"
    return reported[room_id]


@asynccontextmanager
async def copilot_runtime(
    config: Any, *, room_id: str
) -> AsyncIterator[tuple[Any, str]]:
    """A fresh ``copilot --acp`` for ``room_id``, spawned by a fresh adapter's
    own runtime wiring (launcher, env, workspace) and stopped on exit.

    Yields the started runtime and the room's workspace."""
    from band.adapters.copilot_acp import (  # noqa: PLC0415 -- the acp extra, as above
        CopilotACPAdapter,
    )

    spawner = CopilotACPAdapter(config)
    room = spawner._room(room_id)
    runtime = spawner._build_runtime(room, room.generation)
    await runtime.start()  # A failed start closes its own process.
    try:
        yield runtime, room.workspace
    finally:
        await runtime.stop()


async def copilot_persisted_selection(
    config: Any, *, room_id: str, session_id: str
) -> ModelSelection:
    """What a fresh ``copilot --acp`` reports ``session_id`` runs after
    ``session/load``: Copilot's own record, not the adapter's."""
    from band.integrations.acp.model_selection import (  # noqa: PLC0415 -- the acp extra, as above
        locate_model_options,
    )
    from band.integrations.acp.session_config import (  # noqa: PLC0415 -- the acp extra, as above
        session_config_options,
    )

    async with copilot_runtime(config, room_id=room_id) as (runtime, workspace):
        loaded = await runtime.load_session_response(
            cwd=workspace, session_id=session_id, mcp_servers=[]
        )
    assert loaded is not None, f"Copilot could not load session {session_id}"
    return locate_model_options(
        session_config_options(loaded) or ()
    ).current_selection()


async def advertised_switch_target(
    probe: Any, *, away_from: str
) -> ModelSelection | None:
    """A model other than ``away_from``, with an effort other than that
    model's default, as a fresh hosted session advertises them.

    Copilot's own default model is tried first: it is the account's agent
    model, so the turn that runs on it replies reliably. ``probe`` needs its
    own ``COPILOT_HOME``, since walking the catalog selects models in it.
    ``None`` when no advertised model qualifies.
    """
    from band.integrations.acp.model_selection import (  # noqa: PLC0415 -- the acp extra, as above
        locate_model_options,
    )
    from band.integrations.acp.session_config import (  # noqa: PLC0415 -- the acp extra, as above
        select_values,
    )

    async with copilot_runtime(probe, room_id=CATALOG_PROBE_ROOM) as (
        runtime,
        workspace,
    ):
        session_id = await runtime.create_session(cwd=workspace, mcp_servers=[])
        model = locate_model_options(runtime.config_options(session_id)).model
        assert model is not None, "a Copilot-hosted session advertises no model select"
        advertised = select_values(model)
        assert away_from in advertised, (
            f'COPILOT_HOSTED_MODEL "{away_from}" is not advertised; '
            f"{available(advertised)}"
        )
        for candidate in dict.fromkeys((model.current_value, *advertised)):
            if candidate == away_from:
                continue
            await runtime.set_config_option(
                session_id=session_id, config_id=model.id, value=candidate
            )
            effort = non_default_effort(runtime.config_options(session_id))
            if effort is not None:
                return ModelSelection(model=candidate, reasoning_effort=effort)
    return None


def non_default_effort(options: Any) -> str | None:
    """An effort the session's current model advertises besides its default."""
    from band.integrations.acp.model_selection import (  # noqa: PLC0415 -- the acp extra, as above
        locate_model_options,
    )
    from band.integrations.acp.session_config import (  # noqa: PLC0415 -- the acp extra, as above
        select_values,
    )

    effort = locate_model_options(options).effort
    if effort is None:
        return None
    return next(
        (value for value in select_values(effort) if value != effort.current_value),
        None,
    )
