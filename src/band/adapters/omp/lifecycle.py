"""Acquire, configure and retire room-owned OMP sessions."""

from __future__ import annotations

import asyncio
import collections.abc
import logging
from collections.abc import Awaitable, Callable, Iterable
from contextvars import Context
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import TypeVar, cast

from pydantic import BaseModel, JsonValue

from band.adapters.omp.contract import OmpAdapterContract
from band.adapters.omp.roomstate import (
    OMP_SESSION_DIRECTORY,
    OmpRoom,
    _selection_after,
)
from band.converters.omp import OmpSessionState
from band.core.model_catalog import ModelSelection
from band.core.retention import ReleaseCause, ReleaseOutcome, Residency
from band.core.retention.cleanup import report_owned
from band.core.turn_lifecycle import retire_provider_timeout
from band.integrations.omp import OMP_THINKING_LEVEL_AUTO, omp_model_parts
from band.integrations.ompprocess import (
    OMP_SESSION_DIR_FLAG,
    OmpMcpServer,
    omp_mcp_arguments,
    omp_mcp_plugin,
    omp_mcp_ready,
)
from band.integrations.omprpc import OmpProcessError, OmpRpcClient, OmpRpcError
from band.integrations.ompwire import OmpCommandName, OmpState
from band.runtime.tools import BAND_MCP_SERVER_NAME

logger = logging.getLogger(__name__)

MODEL_SELECTION_INTERRUPTED = "Native model selection was interrupted"
T = TypeVar("T")


class ThinkingLevels(BaseModel):
    levels: tuple[str, ...]


class SessionChange(BaseModel):
    cancelled: bool


class AskDialog(BaseModel):
    enabled: bool


class OmpRoomLifecycleMixin(OmpAdapterContract):
    async def _connection_mcp_servers(
        self, room: OmpRoom
    ) -> collections.abc.Mapping[str, OmpMcpServer]:
        """The external MCP servers for one new native connection.

        Without a resolver this is the static config. With one, its result is the
        complete external set for this connection, used for both the overlay and
        native readiness; a failure propagates rather than falling back to config.
        """
        if self._resolve_mcp_servers is None:
            return self.config.mcp_servers
        servers = dict(await self._resolve_mcp_servers(room.workspace))
        task = asyncio.current_task()
        if (
            room.retained.closing
            or (task is not None and task.cancelling())
            or (room.deadline is not None and room.deadline.expired())
        ):
            raise asyncio.CancelledError
        for name, server in servers.items():
            if not isinstance(name, str) or not isinstance(server, OmpMcpServer):
                raise TypeError("resolve_mcp_servers must map names to OmpMcpServer")
            if name == BAND_MCP_SERVER_NAME:
                # OMP mints this server's tools under Band's own tool names.
                raise ValueError(
                    f"MCP server name {BAND_MCP_SERVER_NAME!r} is reserved for Band tools"
                )
        protected = self.config.mcp_servers.get(BAND_MCP_SERVER_NAME)
        if protected is not None:
            # The configured Band endpoint is the SDK's own channel, not an external
            # server, so it is kept beside whatever external set was resolved.
            servers = {BAND_MCP_SERVER_NAME: protected, **servers}
        return MappingProxyType(servers)

    def _room(self, room_id: str) -> OmpRoom:
        room = self._rooms.get(room_id)
        if room is None:
            room = OmpRoom(room_id)
            room.retained = self._retention.register(
                room_id,
                acquire=partial(self._acquire_room, room),
                release=partial(self._release_room, room),
                dispose=partial(self._dispose_room, room),
            )
            self._rooms[room_id] = room
        return room

    async def _prepare_room(self, room: OmpRoom, history: OmpSessionState) -> None:
        await self._exclusive(
            room.room_id, partial(self._prepare_connection, room, history)
        )

    async def _prepare_connection(
        self, room: OmpRoom, history: OmpSessionState
    ) -> None:
        if room.runtime is not None:
            return
        room.history = history
        room.workspace = self._workspaces.claim(room.room_id)
        room.servers = await self._connection_mcp_servers(room)

    async def _acquire_room(self, room: OmpRoom, generation: int, resume: bool) -> None:
        room_id, workspace = room.room_id, room.workspace
        room.session_id = ""
        room.session_file = None
        room.bootstrapped = False
        room.applied_selection = None
        try:
            plugin = room.resources.enter_context(
                omp_mcp_plugin(
                    room.servers,
                    workspace=workspace,
                    system_prompt=self._build_system_context(),
                )
            )
            selection = self._selection_for_room(room_id)
            command = self._spawn_command(workspace, selection)
            session_dir = Path(workspace) / OMP_SESSION_DIRECTORY
            session_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            command.extend((OMP_SESSION_DIR_FLAG, str(session_dir)))
            if plugin:
                command.extend(omp_mcp_arguments(plugin))
            room.runtime = OmpRpcClient(command, env=self._spawn_env(selection))
            room.runtime.schedule_callback = partial(self._exclusive, room_id)
            room.retiring = False
            await room.client.start()
            if plugin:
                await omp_mcp_ready(room.client, plugin, room.servers)
            candidate = room.history.room_to_session.get(room_id)
            saved = Path(candidate) if candidate else None
            if (
                saved is not None
                and saved.resolve().is_relative_to(session_dir.resolve())
                and saved.is_file()
            ):
                try:
                    change = SessionChange.model_validate(
                        await room.client.request(
                            OmpCommandName.SWITCH_SESSION, sessionPath=candidate
                        )
                    )
                    room.bootstrapped = not change.cancelled
                    if change.cancelled:
                        logger.warning(
                            "OMP session restore was cancelled; replaying room history"
                        )
                except OmpRpcError:
                    logger.warning(
                        "OMP session could not be restored; replaying room history",
                        exc_info=True,
                    )
                    change = SessionChange.model_validate(
                        await room.client.request(OmpCommandName.NEW_SESSION)
                    )
                    if change.cancelled:
                        raise RuntimeError(
                            "OMP refused to create a replacement session"
                        )
            elif saved is not None:
                logger.warning(
                    "OMP session candidate was not a file in the room session "
                    "directory; replaying room history"
                )
            await room.client.request(
                OmpCommandName.SET_HOST_TOOLS,
                tools=cast(list[JsonValue], self._host_tools()),
            )
            # Room turns answer structured asks through a follow-up message.
            dialog = AskDialog.model_validate(
                await room.client.request(OmpCommandName.SET_ASK_DIALOG, enabled=True)
            )
            if not dialog.enabled:
                raise RuntimeError("OMP refused the structured ask dialog")
            await self._refresh_state(room)
        finally:
            room.servers = {}
            room.history = OmpSessionState()

    async def _native_state(self, room: OmpRoom) -> OmpState:
        return OmpState.model_validate(
            await room.client.request(OmpCommandName.GET_STATE)
        )

    async def _refresh_state(self, room: OmpRoom) -> None:
        state = await self._native_state(room)
        room.session_id = state.sessionId
        room.session_file = state.sessionFile

    async def _select_model(self, room: OmpRoom, selection: ModelSelection) -> None:
        before = await self._native_state(room)
        try:
            await self._apply_native_selection(room, selection)
        except asyncio.CancelledError:
            room.retiring = True
            raise
        except Exception:
            room.retiring = True
            try:
                await self._restore_native_state(room, before)
            except Exception:
                logger.exception("OMP could not restore the previous model selection")
            else:
                room.retiring = False
            raise
        else:
            room.applied_selection = _selection_after(room.applied_selection, selection)
        finally:
            await self._recover_room(room)

    async def _apply_native_selection(
        self, room: OmpRoom, selection: ModelSelection
    ) -> None:
        if selection.model:
            provider, model_id = omp_model_parts(selection.model)
            await room.client.request(
                OmpCommandName.SET_MODEL, provider=provider, modelId=model_id
            )
        effort = selection.reasoning_effort
        if effort is None:
            return
        if effort != OMP_THINKING_LEVEL_AUTO:
            offered = ThinkingLevels.model_validate(
                await room.client.request(OmpCommandName.GET_AVAILABLE_THINKING_LEVELS)
            )
            if effort not in offered.levels:
                raise ValueError(f"OMP does not offer reasoning effort {effort!r}")
        await room.client.request(OmpCommandName.SET_THINKING_LEVEL, level=effort)
        # Native state exposes the effective level, not the automatic selector.
        if effort == OMP_THINKING_LEVEL_AUTO:
            return
        state = await self._native_state(room)
        if state.thinkingLevel != effort:
            raise ValueError("OMP did not apply the requested reasoning effort")

    async def _restore_native_state(self, room: OmpRoom, before: OmpState) -> None:
        prior = room.applied_selection
        if prior is not None and prior.model:
            provider, model_id = omp_model_parts(prior.model)
            await room.client.request(
                OmpCommandName.SET_MODEL, provider=provider, modelId=model_id
            )
        elif before.model is not None:
            await room.client.request(
                OmpCommandName.SET_MODEL,
                provider=before.model.provider,
                modelId=before.model.id,
            )
        effort = (
            prior.reasoning_effort
            if prior is not None and prior.reasoning_effort is not None
            else (before.thinkingLevel or OMP_THINKING_LEVEL_AUTO)
        )
        await room.client.request(OmpCommandName.SET_THINKING_LEVEL, level=effort)

    async def apply_model_selection(
        self, selection: ModelSelection, *, room_id: str
    ) -> None:
        await self._exclusive(
            room_id, partial(self._apply_selection, room_id, selection)
        )

    def _selection_for_room(self, room_id: str) -> ModelSelection:
        return self._room_selections.get(room_id, self.model_selection)

    async def _apply_selection(self, room_id: str, selection: ModelSelection) -> None:
        room = self._rooms.get(room_id)
        if (
            room is not None
            and room.runtime is not None
            and room.retained.snapshot.state == Residency.RESIDENT
            and not room.retained.closing
            and not room.retiring
        ):
            await self._select_model(room, selection)
        self._room_selections[room_id] = _selection_after(
            self._selection_for_room(room_id), selection
        )

    async def _reconcile_selection(self, room: OmpRoom) -> None:
        selection = self._selection_for_room(room.room_id)
        if selection != room.applied_selection:
            await self._select_model(room, selection)

    async def _schedule_prompt(
        self, room: OmpRoom, write: Callable[[], Awaitable[None]]
    ) -> None:
        await self._exclusive(room.room_id, partial(self._admit_prompt, room, write))

    async def _admit_prompt(
        self, room: OmpRoom, write: Callable[[], Awaitable[None]]
    ) -> None:
        self._require_prompt_authority(room)
        await self._reconcile_selection(room)
        self._require_prompt_authority(room)
        await write()

    @staticmethod
    def _require_prompt_authority(room: OmpRoom) -> None:
        if room.retiring or not room.retained.has_authority(room.retained.generation):
            raise OmpProcessError(MODEL_SELECTION_INTERRUPTED)

    async def _exclusive(self, room_id: str, run: Callable[[], Awaitable[T]]) -> T:
        """Serialize provider mutations, inlining reentry from the current job."""
        if self._mailbox.occupying(room_id):
            return await run()
        return await self._mailbox.ask(room_id, run)

    async def _recover_room(self, room: OmpRoom) -> None:
        if room.retiring:
            Context().run(
                room.retained.recover,
                generation=room.retained.generation,
                reason=MODEL_SELECTION_INTERRUPTED,
            )

    def _request_disposal(self, room: OmpRoom) -> asyncio.Task[None] | None:
        if room.deadline is not None:
            fired = retire_provider_timeout(room.deadline)
            if fired and room.operation is not None:
                room.retained.begin_settlement(room.operation)
        # Retention transitions outlive the provider job that requests cleanup.
        room.disposal = Context().run(room.retained.request_disposal)
        return room.disposal

    async def _join_disposal(self, room: OmpRoom) -> None:
        if room.disposal is not None:
            try:
                await report_owned(room.disposal, None)
            except Exception:
                logger.exception("OMP failed to close the room after a turn failure")
                raise

    def _finalize_selection(self, room: OmpRoom) -> None:
        if room.clear_selection:
            self._room_selections.pop(room.room_id, None)
            room.clear_selection = False

    async def _release_room(self, room: OmpRoom, cause: ReleaseCause) -> ReleaseOutcome:
        room.retiring = True
        try:
            if room.runtime is not None:
                # Failed retirement must surface before joining its callback's job.
                room.client.raise_if_retirement_unproven()
            return await self._exclusive(
                room.room_id, partial(self._close_connection, room)
            )
        except BaseException:
            room.clear_selection = False
            raise

    async def _close_connection(self, room: OmpRoom) -> ReleaseOutcome:
        if room.runtime is not None:
            await room.client.close()
        room.resources.close()
        room.runtime = None
        self._finalize_selection(room)
        return ReleaseOutcome.RELEASED

    async def _dispose_room(self, room: OmpRoom) -> None:
        await self._exclusive(room.room_id, partial(self._forget_room, room))

    async def _forget_room(self, room: OmpRoom) -> None:
        self._finalize_selection(room)
        room.servers = {}
        self._workspaces.release(room.room_id)
        if self._rooms.get(room.room_id) is room:
            del self._rooms[room.room_id]

    async def on_cleanup(self, room_id: str) -> None:
        room = self._room(room_id)
        if not room.retiring:
            room.clear_selection = True
        inside_job = self._mailbox.occupying(room_id)
        if inside_job and room.operation is not None:
            room.retained.begin_settlement(room.operation)
        attempt = self._request_disposal(room)
        if (
            attempt is not None
            and not inside_job
            and not room.retained.enrolls(asyncio.current_task())
        ):
            await report_owned(attempt, None)

    def _cleanup_rooms(self) -> Iterable[Awaitable[None]]:
        return (self.on_cleanup(room_id) for room_id in tuple(self._rooms))

    async def cleanup_all(self) -> None:
        results = await asyncio.gather(*self._cleanup_rooms(), return_exceptions=True)
        failures = [result for result in results if isinstance(result, BaseException)]
        if not failures:
            return
        if len(failures) == 1:
            raise failures[0]
        raise BaseExceptionGroup("OMP room cleanup failed", failures)
