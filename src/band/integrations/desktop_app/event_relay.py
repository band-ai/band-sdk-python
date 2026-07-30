"""One Band WebSocket shared by Claude Desktop's stdio MCP processes."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import logging
import os
from collections import defaultdict
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Callable, Iterator, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from band.platform.event import PlatformEvent
from band.platform.link import BandLink
from band.runtime.presence import RoomPresence

logger = logging.getLogger(__name__)

STATE_DIR = Path.home() / "Library" / "Caches" / "band-sdk"


class RelayTuning(BaseSettings):
    """Per-install knobs for the shared-WebSocket relay."""

    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    band_relay_start_timeout_s: float = Field(
        30,
        ge=1,
        description="How long start() waits for a first leader or follower role.",
    )
    band_relay_retry_delay_s: float = Field(
        1,
        gt=0,
        description="First backoff after the relay's transport fails.",
    )
    band_relay_max_retry_delay_s: float = Field(
        60,
        ge=1,
        description="Backoff ceiling while another consumer holds the agent key.",
    )
    band_relay_fanout_timeout_s: float = Field(
        5,
        gt=0,
        description="How long one follower may take to accept a fanned-out line.",
    )


RELAY_TUNING = RelayTuning()


class Fanout(StrEnum):
    """What a leader tells its followers, named once for both ends of the wire.

    Each line is ``<kind> <room id>``: a follower has no socket of its own, so
    everything it knows about the platform arrives this way.
    """

    EVENT = "event"
    JOINED = "joined"


async def deliver(writer: asyncio.StreamWriter, payload: bytes) -> bool:
    """Hand one fanout line to a follower, or report it unusable.

    Bounded on purpose. This runs on the leader's WebSocket event path, so a
    follower that stopped reading — a frozen Desktop, a stopped process still
    holding its end — would otherwise stall the send queue it shares with every
    other follower, and starve them all of events rather than only itself.
    """
    try:
        async with asyncio.timeout(RELAY_TUNING.band_relay_fanout_timeout_s):
            writer.write(payload)
            await writer.drain()
        return True
    except (ConnectionError, RuntimeError, TimeoutError):
        return False


class RelayStatus(BaseModel):
    """How this process is currently receiving Band room events."""

    role: Literal["starting", "leader", "follower"] = "starting"
    websocket_connected: bool = False
    events_received: int = 0
    rooms_added: list[str] = Field(default_factory=list)
    last_error: str | None = None

    @property
    def live(self) -> bool:
        """Whether room events can still reach this process without polling."""
        return self.role == "follower" or self.websocket_connected

    @property
    def warning(self) -> str:
        """What the agent should be told when events stop arriving live."""
        if self.live:
            return ""
        return (
            "Warning: this agent's Band WebSocket is down "
            f"({self.last_error or 'reason unknown'}), so room events arrive "
            "late by REST and the agent shows offline in Band. Another process "
            "is probably using the same agent key."
        )


class RoomEventBroker:
    """Wake local room-view waits when a room event arrives."""

    def __init__(self) -> None:
        self._versions: defaultdict[str, int] = defaultdict(int)
        self._conditions: defaultdict[str, asyncio.Condition] = defaultdict(
            asyncio.Condition
        )

    def version(self, chat_id: str) -> int:
        return self._versions[chat_id]

    async def publish(self, chat_id: str) -> None:
        condition = self._conditions[chat_id]
        async with condition:
            self._versions[chat_id] += 1
            condition.notify_all()

    async def wait(
        self,
        chat_id: str,
        *,
        after_version: int,
        timeout_seconds: int,
    ) -> bool:
        condition = self._conditions[chat_id]
        try:
            async with asyncio.timeout(timeout_seconds):
                async with condition:
                    await condition.wait_for(
                        lambda: self._versions[chat_id] > after_version
                    )
            return True
        except TimeoutError:
            return False


class DesktopRoomEventRelay:
    """Elect one local WS owner and fan room events out to sibling processes."""

    def __init__(
        self,
        *,
        agent_id: str,
        agent_key: str,
        rest_url: str,
        ws_url: str,
        state_dir: Path | None = None,
        link_factory: Callable[..., BandLink] = BandLink,
        presence_factory: Callable[[BandLink], RoomPresence] = RoomPresence,
    ) -> None:
        digest = hashlib.sha256(agent_id.encode()).hexdigest()[:16]
        directory = state_dir or STATE_DIR
        self._directory = directory
        self._lock_path = directory / f"desktop-{digest}.lock"
        self._socket_path = directory / f"desktop-{digest}.sock"
        self._agent_id = agent_id
        self._agent_key = agent_key
        self._rest_url = rest_url
        self._ws_url = ws_url
        self._link_factory = link_factory
        self._presence_factory = presence_factory
        self.events = RoomEventBroker()
        self.status = RelayStatus()
        self._ready = asyncio.Event()
        self._stopped = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._supervise())
        try:
            async with asyncio.timeout(RELAY_TUNING.band_relay_start_timeout_s):
                await self._ready.wait()
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _supervise(self) -> None:
        """Hold the WebSocket, or follow whoever does, until told to stop.

        Both roles end when their transport does — a superseded WebSocket for
        the leader, a closed socket for a follower — so this loop re-elects
        rather than leaving a dead connection in place. Repeated failures back
        off, because the usual cause is another consumer of the same agent key
        rate-limiting us.
        """
        delay = RELAY_TUNING.band_relay_retry_delay_s
        while not self._stopped.is_set():
            # Claiming leadership is inside the retry: if the lock file cannot
            # be opened or locked, this loop must back off and try again, not
            # die and leave the process with no event delivery at all.
            try:
                with self._leadership() as leading:
                    await (self._lead() if leading else self._follow())
                delay = RELAY_TUNING.band_relay_retry_delay_s
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Desktop room event relay restarting", exc_info=True)
                self.status.last_error = f"{type(error).__name__}: {error}"
                delay = min(
                    getattr(error, "retry_after", None) or delay * 2,
                    RELAY_TUNING.band_relay_max_retry_delay_s,
                )
            if not self._stopped.is_set():
                await asyncio.sleep(delay)

    @contextmanager
    def _leadership(self) -> Iterator[bool]:
        """Hold the per-agent leader lock for the block, if it is free."""
        descriptor = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        acquired = False
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                pass
            yield acquired
        finally:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    async def _lead(self) -> None:
        self._socket_path.unlink(missing_ok=True)
        clients: set[asyncio.StreamWriter] = set()

        def line(kind: Fanout, room_id: str) -> bytes:
            return f"{kind} {room_id}\n".encode()

        async def accept(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            del reader
            clients.add(writer)
            try:
                # The ready line, then the rooms this leader already knows the
                # agent was added to, so a follower that connects afterwards
                # can still report them.
                writer.write(b"\n")
                for room_id in self.status.rooms_added:
                    writer.write(line(Fanout.JOINED, room_id))
                await writer.drain()
                await self._stopped.wait()
            finally:
                clients.discard(writer)
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_unix_server(accept, path=self._socket_path)
        self._socket_path.chmod(0o600)
        link = self._link_factory(
            agent_id=self._agent_id,
            api_key=self._agent_key,
            ws_url=self._ws_url,
            rest_url=self._rest_url,
        )
        presence = self._presence_factory(link)

        async def fanout(payload: bytes) -> None:
            for writer in list(clients):
                if await deliver(writer, payload):
                    continue
                logger.warning("Dropping a follower that stopped reading events")
                clients.discard(writer)
                writer.close()

        async def publish(room_id: str, _: PlatformEvent) -> None:
            self.status.events_received += 1
            await self.events.publish(room_id)
            await fanout(line(Fanout.EVENT, room_id))

        async def joined(room_id: str, _: dict) -> None:
            self._record_room_added(room_id)
            await fanout(line(Fanout.JOINED, room_id))

        presence.on_room_joined = joined
        lost = asyncio.Event()

        async def report_lost() -> None:
            reason = getattr(link, "last_disconnect_reason", None)
            self.status.last_error = f"WebSocket lost: {reason or 'disconnected'}"
            logger.warning("Desktop relay lost its WebSocket: %s", reason)
            lost.set()

        presence.on_disconnected = report_lost
        presence.on_room_event = publish
        try:
            await presence.start()
            self.status.role = "leader"
            self.status.websocket_connected = True
            self.status.last_error = None
            logger.info("relay leading agent=%s", self._agent_id)
            self._ready.set()
            await self._hold_leadership(lost)
        finally:
            self.status.websocket_connected = False
            await presence.stop()
            await link.disconnect()
            server.close()
            await server.wait_closed()
            for writer in clients:
                writer.close()
            await asyncio.gather(
                *(writer.wait_closed() for writer in clients),
                return_exceptions=True,
            )
            self._socket_path.unlink(missing_ok=True)

    async def _hold_leadership(self, lost: asyncio.Event) -> None:
        """Lead until told to stop or the platform ends this connection.

        Purely event-driven: a terminal disconnect (another consumer of the
        same agent key superseding us) reaches here through RoomPresence's
        on_disconnected hook, so leadership is relinquished the moment it
        happens rather than on the next timer check.
        """
        waiters = {
            asyncio.create_task(self._stopped.wait()),
            asyncio.create_task(lost.wait()),
        }
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

    def _record_room_added(self, room_id: str) -> None:
        """Note a room the agent was added to, for the agent to mention once."""
        if room_id not in self.status.rooms_added:
            self.status.rooms_added.append(room_id)

    async def _follow(self) -> None:
        try:
            reader, writer = await asyncio.open_unix_connection(self._socket_path)
        except (ConnectionError, FileNotFoundError):
            return

        try:
            await reader.readline()
            self.status.role = "follower"
            logger.info("relay following agent=%s", self._agent_id)
            self._ready.set()
            while line := await reader.readline():
                kind, _, room_id = line.decode().strip().partition(" ")
                if not room_id:
                    continue
                match kind:
                    case Fanout.JOINED:
                        self._record_room_added(room_id)
                    case Fanout.EVENT:
                        self.status.events_received += 1
                        await self.events.publish(room_id)
        finally:
            self.status.role = "starting"
            writer.close()
            await writer.wait_closed()
