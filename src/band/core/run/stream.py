"""Async observation view over an ``EventSink``.

Backends emit via ``context.events``; consumers iterate envelopes here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from collections.abc import Callable
from band.core.backends.oneshot import TurnTarget, execute_turn
from band.core.contracts import EnvelopedTurnEvent, RunFailedEvent
from band.core.exceptions import BandConnectionError, RunFailed, StreamError
from band.core.protocols import AgentToolsProtocol, CancellationToken
from band.core.run.cancellation import (
    AnyCancellation,
    FlagCancellation,
    NeverCancelled,
)
from band.core.run.context import SimpleRunContext
from band.core.run.sink import RecordingEventSink
from band.core.contracts import RunResult
from band.core.types import AgentInput

_END: object = object()


@dataclass
class AgentStream:
    """Async view over a sink's envelopes, waiting for each as it is emitted.

    Deterministic cleanup requires ``async with`` or ``aclose``; bare
    ``async for`` is best-effort.

    Built by ``observe`` or ``live_from_sink`` — both subscribe to the sink,
    which is what makes iteration live, so there is no way to hold one that
    isn't.
    """

    _sink: RecordingEventSink
    _queue: asyncio.Queue[EnvelopedTurnEvent | object] = field(repr=False)
    _unsubscribe: Callable[[], None] | None = field(repr=False)
    _closed: bool = field(default=False, init=False)
    _producer: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _cancellation: FlagCancellation | NeverCancelled = field(
        default_factory=NeverCancelled, repr=False, init=False
    )
    _terminal_error: BaseException | None = field(default=None, init=False, repr=False)
    _result: RunResult | None = field(default=None, init=False, repr=False)

    @classmethod
    def live_from_sink(cls, sink: RecordingEventSink) -> AgentStream:
        """Build a stream that waits for new sink envelopes."""
        queue: asyncio.Queue[EnvelopedTurnEvent | object] = asyncio.Queue()
        unsubscribe = sink.add_observer(lambda enveloped: queue.put_nowait(enveloped))
        return cls(_sink=sink, _queue=queue, _unsubscribe=unsubscribe)

    @classmethod
    def observe(
        cls,
        adapter: TurnTarget,
        inp: AgentInput,
        *,
        tools: AgentToolsProtocol,
        cancellation: CancellationToken | None = None,
    ) -> AgentStream:
        """Start a turn in the background and return a live stream.

        ``adapter`` is a ``FrameworkAdapter`` / ``SimpleAdapter``, or a test
        ``TurnRunner`` with ``.run`` (e.g. a bare native loop façade).

        Model/execution failures are emitted as ``RunFailedEvent`` (and the
        stream ends normally). Transport failures set a ``StreamError`` that
        ``__anext__`` raises. Cancel via ``async with`` / ``aclose``.
        """
        sink = RecordingEventSink()
        # aclose() needs a lever of its own, and only FlagCancellation has
        # one. A caller's token keeps its say by being read alongside it
        # rather than replaced — it is usually the only one that knows about
        # the caller's own interrupts.
        lever = cancellation if isinstance(cancellation, FlagCancellation) else None
        token = lever or FlagCancellation()
        run_cancellation: CancellationToken = (
            token
            if cancellation is None or cancellation is token
            else AnyCancellation((token, cancellation))
        )

        stream = cls.live_from_sink(sink)
        stream._cancellation = token

        async def _produce() -> None:
            context = SimpleRunContext(
                tools=tools, events=sink, cancellation=run_cancellation
            )
            try:
                stream._result = await execute_turn(adapter, inp, context=context)
            except asyncio.CancelledError:
                raise
            except BandConnectionError as exc:
                stream._terminal_error = StreamError(str(exc))
            except Exception as exc:
                failed = RunFailed(str(exc), retryable=False)
                await sink.emit(
                    RunFailedEvent(
                        message=str(failed),
                        retryable=failed.retryable,
                        error_type=type(exc).__name__,
                        partial_text=failed.partial_text,
                    )
                )
            finally:
                await stream._queue.put(_END)

        stream._producer = asyncio.create_task(_produce())
        return stream

    def __aiter__(self) -> AgentStream:
        return self

    async def __anext__(self) -> EnvelopedTurnEvent:
        if self._closed:
            raise StopAsyncIteration

        item = await self._queue.get()
        if item is not _END:
            return item  # type: ignore[no-any-return]

        self._detach()
        if self._terminal_error is not None:
            err, self._terminal_error = self._terminal_error, None
            raise err
        raise StopAsyncIteration

    @property
    def result(self) -> RunResult | None:
        """``RunResult`` when the producer finished successfully."""
        return self._result

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._detach()
        if isinstance(self._cancellation, FlagCancellation):
            self._cancellation.cancel()
        producer = self._producer
        if producer is not None and not producer.done():
            producer.cancel()
            try:
                await producer
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    def _detach(self) -> None:
        unsubscribe, self._unsubscribe = self._unsubscribe, None
        if unsubscribe is not None:
            unsubscribe()
        try:
            self._queue.put_nowait(_END)
        except Exception:
            pass

    async def __aenter__(self) -> AgentStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
