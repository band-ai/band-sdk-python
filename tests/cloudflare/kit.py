"""External Cloudflare container doubles exercised inside the real Python Worker runtime."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import js
from pyodide.ffi import create_proxy
from worker import PROXY_KEY, SECRET_SETTINGS
from workers import Request, Response

SETTINGS = {
    "BAND_AGENT_ID": "agent",
    "BAND_REST_URL": "https://app.band.ai",
    "BAND_API_KEY": "private-agent",
    "ANTHROPIC_API_KEY": "private-provider",
    "BRIDGE_TOKEN": "private-bridge",
    "VERSION": SimpleNamespace(id="version"),
}
IMAGE = "qualified-image"
BODY = "original body"


class Container:
    def __init__(self) -> None:
        self.running = False
        self.images = SimpleNamespace(base=IMAGE)
        self.calls: list[str] = []
        self.ping_entered = asyncio.Event()
        self.release_ping = asyncio.Event()
        self.restored = asyncio.Event()
        self.stopped = asyncio.get_running_loop().create_future()
        self.aborted = False
        self.options: Any = None
        self.fail_forward = False

    async def interceptOutboundHttps(self, host: str, injector: Any) -> None:
        self.calls.append("intercept:" + host)

    def start(self, options: Any) -> None:
        assert not self.running
        self.running = True
        self.options = options
        self.calls.append("start")
        assert options.enableInternet is False
        assert options.env.BAND_API_KEY == PROXY_KEY
        assert options.env.ANTHROPIC_API_KEY == PROXY_KEY
        for name in SECRET_SETTINGS:
            assert SETTINGS[name] not in str(js.JSON.stringify(options))

    async def setInactivityTimeout(self, timeout: int) -> None:
        self.calls.append("idle")
        self.restored.set()

    def monitor(self) -> asyncio.Future[None]:
        return self.stopped

    async def destroy(self) -> None:
        self.calls.append("destroy")
        self.running = False
        if not self.stopped.done():
            self.stopped.set_result(None)

    async def inspect(self) -> Any:
        return SimpleNamespace(image=IMAGE)

    def getTcpPort(self, port: int) -> Container:
        return self

    async def fetch(self, request: Any, options: Any = None) -> Any:
        if isinstance(request, str):
            assert urlsplit(request).path == "/ping"
            cancelled = asyncio.Event()
            callback = create_proxy(lambda *_: cancelled.set())
            options.signal.addEventListener("abort", callback)
            ping = asyncio.create_task(self.release_ping.wait())
            abort = asyncio.create_task(cancelled.wait())
            self.ping_entered.set()
            try:
                await asyncio.wait((ping, abort), return_when=asyncio.FIRST_COMPLETED)
                if options.signal.aborted:
                    self.aborted = True
                    raise RuntimeError("Readiness aborted")
                return Response("healthy").js_object
            finally:
                options.signal.removeEventListener("abort", callback)
                callback.destroy()
                for task in (ping, abort):
                    task.cancel()
                await asyncio.gather(ping, abort, return_exceptions=True)
        assert urlsplit(request.url).scheme == "http"
        assert not request.headers.has("Authorization")
        self.calls.append("forward")
        if self.fail_forward:
            raise RuntimeError(
                " ".join(SETTINGS[name] for name in SECRET_SETTINGS)
                + " upstream failed"
            )
        return Response(await request.text(), status=201).js_object


class Routing:
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
