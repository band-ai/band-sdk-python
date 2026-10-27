"""External Cloudflare boundaries doubled inside the real Python Worker runtime."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import js
from contract import BODY, DESTROY_FAILURE, IMAGE, SETTINGS, VERSION_ID, Call
from pyodide.ffi import create_proxy
from worker import CONTAINER_IMAGE_HEADER, ROOM_VERSION_HEADER, SECRET_SETTINGS
from workers import Request, Response

HEALTHY = 200


class Container:
    """A room container whose RPCs settle only when the scenario allows them to."""

    def __init__(self, *, running: bool = False) -> None:
        self.running = running
        self.images = SimpleNamespace(base=IMAGE)
        self.calls: list[Call] = []
        self.injectors: dict[str, Any] = {}
        self.options: Any = None
        self.forwarded: list[dict[str, Any]] = []
        self.run = js.Promise.withResolvers()
        self.admission = asyncio.Event()
        self.admission.set()
        self.intercepting = asyncio.Event()
        self.idle_applied = asyncio.Event()
        self.ping_ready = asyncio.Event()
        self.ping_ready.set()
        self.ping_entered = asyncio.Event()
        self.unready_answers: list[int] = []
        self.fail_forward = False
        self.fail_destroy = False

    async def interceptOutboundHttps(self, host: str, injector: Any) -> None:
        self.calls.append(Call.INTERCEPT)
        self.injectors[host] = injector
        self.intercepting.set()
        await self.admission.wait()

    def start(self, options: Any) -> None:
        self.calls.append(Call.START)
        self.options = options
        self.running = True
        self.run = js.Promise.withResolvers()

    async def setInactivityTimeout(self, timeout: int) -> None:
        self.calls.append(Call.IDLE)
        self.idle_applied.set()

    def monitor(self) -> Any:
        self.calls.append(Call.MONITOR)
        return self.run.promise

    def exit(self, error: Any = None) -> None:
        """Stop the receiver; Cloudflare rejects monitor() when it crashed."""
        self.running = False
        if error is None:
            self.run.resolve(None)
        else:
            self.run.reject(error)

    async def destroy(self) -> None:
        self.calls.append(Call.DESTROY)
        if self.fail_destroy:
            await js.Promise.reject(js.Error.new(DESTROY_FAILURE))
        if self.running:
            self.exit()

    async def inspect(self) -> Any:
        return SimpleNamespace(image=IMAGE)

    def getTcpPort(self, port: int) -> Container:
        return self

    async def fetch(self, request: Any, options: Any = None) -> Any:
        if isinstance(request, str):
            return await self.ping(options.signal)
        return await self.forward(request)

    async def ping(self, signal: Any) -> Any:
        self.calls.append(Call.PING)
        self.ping_entered.set()
        await settled_or_aborted(self.ping_ready, signal)
        if signal.aborted:
            # An aborted workerd fetch rejects with the signal's reason.
            await js.Promise.reject(signal.reason)
        status = self.unready_answers.pop(0) if self.unready_answers else HEALTHY
        return Response("", status=status).js_object

    async def forward(self, request: Any) -> Any:
        self.calls.append(Call.FORWARD)
        self.forwarded.append(
            {"url": request.url, "authorization": request.headers.get("Authorization")}
        )
        if self.fail_forward:
            secrets = " ".join(SETTINGS[name] for name in SECRET_SETTINGS)
            await js.Promise.reject(js.Error.new(f"{secrets} upstream failed"))
        return Response(await request.text(), status=201).js_object


async def settled_or_aborted(gate: asyncio.Event, signal: Any) -> None:
    if signal.aborted:
        return
    aborted = asyncio.Event()
    listener = create_proxy(lambda *_: aborted.set())
    signal.addEventListener("abort", listener)
    waits = [asyncio.ensure_future(gate.wait()), asyncio.ensure_future(aborted.wait())]
    try:
        await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
    finally:
        signal.removeEventListener("abort", listener)
        listener.destroy()
        for wait in waits:
            wait.cancel()
        await asyncio.gather(*waits, return_exceptions=True)


class Routing:
    """The ROOM namespace binding, answering as the addressed room would."""

    def __init__(self) -> None:
        self.room: str | None = None
        self.body: str | None = None

    def getByName(self, room: str) -> Routing:
        self.room = room
        return self

    async def fetch(self, request: Request) -> Response:
        self.body = await request.text()
        return Response(self.body, status=201)


class Clock:
    """Loop time and deadline timers that move only when the scenario advances them."""

    def __init__(self) -> None:
        self.now = 0.0
        self.timers: list[Any] = []

    def time(self) -> float:
        return self.now

    def call_at(self, deadline: float, callback: Any, *args: Any, **kwargs: Any) -> Any:
        timer = SimpleNamespace(callback=callback, cancelled=False)
        timer.cancel = lambda: setattr(timer, "cancelled", True)
        self.timers.append(timer)
        return timer

    def advance(self, seconds: float) -> None:
        self.now += seconds
        for timer in self.timers:
            if not timer.cancelled:
                timer.callback()

    @contextmanager
    def installed(self, loop: asyncio.AbstractEventLoop) -> Iterator[Clock]:
        original = loop.time, loop.call_at
        loop.time, loop.call_at = self.time, self.call_at
        try:
            yield self
        finally:
            loop.time, loop.call_at = original


class LogRecorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.entries: list[list[str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.entries.append([record.levelname, record.getMessage()])


@contextmanager
def recorded_logs(logger: logging.Logger) -> Iterator[list[list[str]]]:
    recorder = LogRecorder()
    logger.addHandler(recorder)
    try:
        yield recorder.entries
    finally:
        logger.removeHandler(recorder)


@contextmanager
def upstream_requests() -> Iterator[list[dict[str, Any]]]:
    """Replace the network with an upstream that records and answers with a redirect."""
    captured: list[dict[str, Any]] = []
    original = js.fetch

    async def upstream(request: Any) -> Any:
        captured.append(
            {
                "url": request.url,
                "key": request.headers.get("x-api-key"),
                "redirect": request.redirect,
            }
        )
        return Response(
            "redirect", status=302, headers={"Location": "https://example.com/"}
        ).js_object

    proxy = create_proxy(upstream)
    js.fetch = proxy
    try:
        yield captured
    finally:
        js.fetch = original
        proxy.destroy()


def environment(settings: dict[str, Any], **bindings: Any) -> SimpleNamespace:
    """Build Worker env from the shared settings; a None override removes a setting."""
    values = {**SETTINGS, **settings, **bindings}
    return SimpleNamespace(
        **{name: value for name, value in values.items() if value is not None},
        VERSION=SimpleNamespace(id=VERSION_ID),
    )


def invocation() -> Request:
    token = SETTINGS["BRIDGE_TOKEN"]
    return Request(
        "https://worker/invocations",
        method="POST",
        body=BODY,
        headers={"Authorization": f"Bearer {token}"},
    )


async def observed(response: Response) -> dict[str, Any]:
    return {
        "status": response.status,
        "body": await response.text(),
        "room_version": response.headers.get(ROOM_VERSION_HEADER),
        "image": response.headers.get(CONTAINER_IMAGE_HEADER),
    }
