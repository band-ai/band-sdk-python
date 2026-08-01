"""Gateway lifecycle helpers (exclusive agent ownership + state machine)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from enum import StrEnum
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Self, TypeVar, cast, get_args

from band.core.exceptions import LifecycleError

if TYPE_CHECKING:
    from band.agent import Agent

logger = logging.getLogger(__name__)


class GatewayState(StrEnum):
    """Lifecycle phases for :class:`GatewayBase`."""

    STOPPED = "stopped"
    STARTING = "starting"
    STARTED = "started"
    SERVING = "serving"


TAdapter = TypeVar("TAdapter")

# agent id(object) -> owning gateway (strong ref so GC cannot reuse owner ids)
_agent_claims: dict[int, object] = {}


def claim_agent(agent: Agent, owner: object) -> None:
    """Claim exclusive lifecycle ownership of ``agent`` for ``owner``.

    Raises:
        LifecycleError: If the agent is already running or claimed by another gateway.
    """
    if agent.is_running:
        raise LifecycleError(
            "Agent is already started; pass a constructed-but-not-started Agent"
        )

    agent_key = id(agent)
    existing = _agent_claims.get(agent_key)
    if existing is not None and existing is not owner:
        raise LifecycleError("Agent is already owned by another gateway")

    _agent_claims[agent_key] = owner


def release_agent(agent: Agent, owner: object) -> None:
    """Release ``agent`` if ``owner`` currently holds the claim."""
    agent_key = id(agent)
    if _agent_claims.get(agent_key) is owner:
        del _agent_claims[agent_key]


def require_adapter(
    agent: Agent,
    adapter_type: type[TAdapter],
    *,
    gateway: str,
) -> TAdapter:
    """Return ``agent.adapter`` typed as ``adapter_type``, or raise ``TypeError``."""
    adapter = agent.adapter
    if not isinstance(adapter, adapter_type):
        raise TypeError(
            f"{gateway} requires {adapter_type.__name__}, got {type(adapter).__name__}"
        )
    return adapter


@contextmanager
def override_attribute(obj: object, name: str, value: object) -> Iterator[None]:
    """Temporarily set ``obj.name = value``; restore the prior value on exit."""
    prior = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, prior)


def raise_collected(
    errors: Sequence[BaseException],
    message: str,
    *,
    primary: BaseException | None = None,
) -> None:
    """Raise ``errors`` as one exception, optionally wrapping a primary cause.

    - ``primary`` set → ``BaseExceptionGroup([primary, *errors]) from primary``
    - single error, no primary → re-raise that error
    - multiple errors → ``BaseExceptionGroup``
    - empty and no primary → no-op
    """
    if primary is not None:
        raise BaseExceptionGroup(message, [primary, *errors]) from primary
    if not errors:
        return
    if len(errors) == 1:
        raise errors[0]
    raise BaseExceptionGroup(message, list(errors))


async def stop_all(
    steps: Sequence[Callable[[], Awaitable[None]]], message: str
) -> None:
    """Run every teardown step, then raise whatever failed.

    Teardown written as a plain sequence of awaits abandons everything after
    the first failure — the transport that could not be closed takes the
    agent, the session and the socket down with it. Each step here runs
    regardless of the ones before it.

    A cancelled step is still cancelled: the remaining releases run, but the
    cancellation is re-raised as itself rather than folded into a group, so
    an enclosing ``asyncio.timeout`` can still recognise it as its own.
    """
    errors: list[BaseException] = []
    for step in steps:
        try:
            await step()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)
    cancelled = next(
        (exc for exc in errors if isinstance(exc, asyncio.CancelledError)), None
    )
    if cancelled is not None:
        for other in errors:
            if other is not cancelled:
                logger.warning("%s (during cancellation)", message, exc_info=other)
        raise cancelled
    raise_collected(errors, message)


class GatewayBase(Generic[TAdapter], ABC):
    """Implements :class:`~band.core.protocols.Gateway` lifecycle.

    Subclasses provide ``_start_resources``, ``_stop_resources``, and
    ``_serve_transport``. Use :meth:`_override` during start to defer
    attribute restores until stop/rollback (backed by an :class:`ExitStack`).

    Declare the owned adapter once as ``GatewayBase[SomeAdapter]``; the
    :attr:`adapter` property validates and returns it typed.
    """

    _adapter_type: ClassVar[type[Any]]

    def __init_subclass__(cls, **kw: Any) -> None:
        super().__init_subclass__(**kw)
        for base in cls.__dict__.get("__orig_bases__", ()):
            args = get_args(base)
            if args and isinstance(args[0], type):
                cls._adapter_type = args[0]
                return
        if not hasattr(cls, "_adapter_type"):
            raise TypeError(f"{cls.__name__} must subclass GatewayBase[SomeAdapter]")

    def __init__(self, agent: Agent) -> None:
        self._agent = agent
        self._state: GatewayState = GatewayState.STOPPED
        self._lifecycle_stack = ExitStack()
        self._lifecycle_lock = asyncio.Lock()

    @property
    def state(self) -> GatewayState:
        return self._state

    @property
    def adapter(self) -> TAdapter:
        return cast(
            TAdapter,
            require_adapter(
                self._agent,
                self._adapter_type,
                gateway=type(self).__name__,
            ),
        )

    def _override(self, obj: object, name: str, value: object) -> None:
        """Set ``obj.name = value`` until the next stop/rollback."""
        self._lifecycle_stack.enter_context(override_attribute(obj, name, value))

    async def _stop_agent(self) -> None:
        """Stop the bound agent if it is running."""
        if self._agent.is_running:
            await self._agent.stop()

    def _reset_lifecycle_stack(self) -> None:
        self._lifecycle_stack.close()
        self._lifecycle_stack = ExitStack()

    async def start(self) -> None:
        """Start gateway-owned resources and the bound agent."""
        async with self._lifecycle_lock:
            if self._state in (GatewayState.STARTED, GatewayState.SERVING):
                return

            claim_agent(self._agent, self)
            self._state = GatewayState.STARTING
            try:
                await self._start_resources()
            except BaseException:
                # Subclass may have started the agent before failing — roll back.
                try:
                    await self._stop_resources()
                except BaseException:
                    logger.exception(
                        "%s rollback after failed start failed", type(self).__name__
                    )
                self._reset_lifecycle_stack()
                release_agent(self._agent, self)
                self._state = GatewayState.STOPPED
                raise

            self._state = GatewayState.STARTED
            logger.debug("%s started", type(self).__name__)

    async def stop(self) -> None:
        """Stop gateway-owned resources and release the agent claim."""
        async with self._lifecycle_lock:
            if self._state == GatewayState.STOPPED:
                return

            errors: list[BaseException] = []
            try:
                await self._stop_resources()
            except BaseException as exc:
                errors.append(exc)
            finally:
                try:
                    self._reset_lifecycle_stack()
                except BaseException as exc:
                    errors.append(exc)
                try:
                    release_agent(self._agent, self)
                except BaseException as exc:
                    errors.append(exc)
                self._state = GatewayState.STOPPED
                logger.debug("%s stopped", type(self).__name__)

            raise_collected(errors, "gateway stop failed")

    async def serve(self) -> None:
        """Run the gateway's blocking serve loop (requires :meth:`start`)."""
        if self._state == GatewayState.STOPPED:
            raise LifecycleError("serve() requires start() first")
        if self._state == GatewayState.SERVING:
            raise LifecycleError("serve() already in progress")

        self._state = GatewayState.SERVING
        try:
            await self._serve_transport()
        except BaseException:
            await self._shutdown_after_serve_failure()
            raise
        else:
            # Normal return (transport exited): stay started for optional restart.
            if self._state == GatewayState.SERVING:
                self._state = GatewayState.STARTED

    async def _shutdown_after_serve_failure(self) -> None:
        """Cancel gateway-owned tasks when ``serve()`` exits unexpectedly."""
        try:
            await self.stop()
        except BaseException:
            logger.exception(
                "%s cleanup after serve failure failed", type(self).__name__
            )

    @abstractmethod
    async def _start_resources(self) -> None:
        """Start gateway-owned resources (and typically the agent)."""

    @abstractmethod
    async def _stop_resources(self) -> None:
        """Stop gateway-owned resources (and typically the agent)."""

    @abstractmethod
    async def _serve_transport(self) -> None:
        """Run the blocking ingress / protocol transport."""

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> bool | None:
        try:
            await self.stop()
        except BaseException as stop_exc:
            if exc is not None:
                raise_collected(
                    [stop_exc],
                    "gateway cleanup failed",
                    primary=exc,
                )
            raise
        return None
