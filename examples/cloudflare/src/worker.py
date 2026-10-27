# /// script
# requires-python = ">=3.12"
# dependencies = ["workers-runtime-sdk==1.9.5"]
# ///
"""Route authenticated invocations to isolated, credential-free Python receivers."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from contextlib import suppress
from typing import Any

import js
from pyodide.ffi import JsException, create_once_callable, to_js
from workers import DurableObject, Request, Response, WorkerEntrypoint

logger = logging.getLogger(__name__)
PORT = 8080
READINESS_SECONDS = 90
IDLE_MS = 180_000
PROXY_KEY = "proxy-managed"
ANTHROPIC_HOST = "api.anthropic.com"
SECRET_SETTINGS = ("BAND_API_KEY", "ANTHROPIC_API_KEY", "BRIDGE_TOKEN")
REQUIRED_SETTINGS = ("BAND_AGENT_ID", "BAND_REST_URL", *SECRET_SETTINGS)


def runtime_options(value: dict[str, Any]) -> Any:
    """Convert nested options for Cloudflare's native runtime APIs."""
    return to_js(value, dict_converter=js.Object.fromEntries)


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def configured(env: Any) -> bool:
    if not all(nonempty(getattr(env, name, None)) for name in REQUIRED_SETTINGS):
        return False
    try:
        endpoint = js.URL.new(env.BAND_REST_URL)
        return bool(
            endpoint.protocol == "https:"
            and endpoint.hostname
            and not endpoint.username
            and not endpoint.password
            and (
                not getattr(env, "ANTHROPIC_MODEL", None)
                or nonempty(env.ANTHROPIC_MODEL)
            )
        )
    except JsException:
        return False


def tagged_response(upstream: Any, headers: dict[str, str]) -> Response:
    response = js.Response.new(upstream.body, upstream)
    for name, value in headers.items():
        response.headers.set(name, value)
    return Response(response)


class KeyInjector(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        target = js.URL.new(request.url)
        if target.protocol != "https:" or target.host != self.ctx.props.host:
            return Response("Forbidden upstream", status=403)
        headers = js.Headers.new(request.js_object.headers)
        headers.set("x-api-key", self.ctx.props.key)
        forwarded = js.Request.new(
            request.js_object,
            runtime_options({"headers": headers, "redirect": "manual"}),
        )
        return Response(await js.fetch(forwarded))


class Room(DurableObject):
    def __init__(self, ctx: Any, env: Any) -> None:
        super().__init__(ctx, env)
        self.booting: asyncio.Task[None] | None = None
        if ctx.container and ctx.container.running:
            ctx.blockConcurrencyWhile(create_once_callable(self.restore_idle))

    async def restore_idle(self) -> None:
        await self.ctx.container.setInactivityTimeout(IDLE_MS)

    async def ensure_ready(self) -> None:
        if self.booting is None:
            self.booting = asyncio.create_task(self.boot())
        await asyncio.shield(self.booting)

    async def boot(self) -> None:
        try:
            await self.start_and_wait()
        finally:
            self.booting = None

    async def start_and_wait(self) -> None:
        container = self.ctx.container
        if not container:
            raise RuntimeError("Container binding missing")
        starting = not container.running
        termination = None
        readiness = None
        abort = js.AbortController.new()
        try:
            if starting:
                await self.start(container)
            await container.setInactivityTimeout(IDLE_MS)
            termination = asyncio.ensure_future(container.monitor())
            termination.add_done_callback(observe_termination)
            readiness = asyncio.create_task(self.wait_for_ping(abort.signal))
            done, _ = await asyncio.wait(
                (readiness, termination), return_when=asyncio.FIRST_COMPLETED
            )
            if termination in done:
                await termination
                raise RuntimeError("Receiver stopped before readiness")
            await readiness
        except BaseException:
            abort.abort()
            if readiness is not None:
                await asyncio.gather(readiness, return_exceptions=True)
            if starting:
                await container.destroy()
                if termination is not None:
                    await asyncio.gather(termination, return_exceptions=True)
            raise
        finally:
            abort.abort()

    async def start(self, container: Any) -> None:
        band_host = js.URL.new(self.env.BAND_REST_URL).host
        for host, key in (
            (band_host, self.env.BAND_API_KEY),
            (ANTHROPIC_HOST, self.env.ANTHROPIC_API_KEY),
        ):
            injector = self.ctx.exports.KeyInjector(
                runtime_options({"props": {"host": host, "key": key}})
            )
            await container.interceptOutboundHttps(host, injector)
        environment = {
            "BAND_AGENT_ID": self.env.BAND_AGENT_ID,
            "BAND_REST_URL": self.env.BAND_REST_URL,
            "BAND_API_KEY": PROXY_KEY,
            "ANTHROPIC_API_KEY": PROXY_KEY,
            "PORT": str(PORT),
        }
        if getattr(self.env, "ANTHROPIC_MODEL", None):
            environment["ANTHROPIC_MODEL"] = self.env.ANTHROPIC_MODEL
        container.start(
            runtime_options(
                {
                    "image": container.images.base,
                    "instance": {"vcpu": 0.25, "memoryMib": 1024, "diskMb": 4000},
                    "enableInternet": False,
                    "env": environment,
                }
            )
        )

    async def wait_for_ping(self, signal: Any) -> None:
        port = self.ctx.container.getTcpPort(PORT)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + READINESS_SECONDS
        while not signal.aborted and loop.time() < deadline:
            controller = js.AbortController.new()
            timer = loop.call_at(deadline, controller.abort)
            try:
                signals = js.AbortSignal.any(to_js([signal, controller.signal]))
                response = await port.fetch(
                    "http://container/ping", runtime_options({"signal": signals})
                )
                if response.body:
                    await response.body.cancel()
                if response.ok:
                    return
            except (JsException, OSError):
                logger.debug("Receiver port not ready")
            finally:
                timer.cancel()
            remaining = deadline - loop.time()
            if remaining > 0:
                await asyncio.sleep(min(0.25, remaining))
        raise RuntimeError("Receiver readiness deadline exceeded")

    async def fetch(self, request: Request) -> Response:
        try:
            await self.ensure_ready()
            target = js.URL.new(request.url)
            target.protocol = "http:"
            target.hostname = "container"
            target.port = ""
            forwarded = js.Request.new(target, request.js_object)
            forwarded.headers.delete("Authorization")
            upstream = await self.ctx.container.getTcpPort(PORT).fetch(forwarded)
            inspection = await self.ctx.container.inspect()
            return tagged_response(
                upstream,
                {
                    "x-band-room-version": self.env.VERSION.id,
                    "x-band-container-image": inspection.image
                    if inspection
                    else "unknown",
                },
            )
        except (JsException, RuntimeError, ValueError) as error:
            detail = str(error)
            for name in SECRET_SETTINGS:
                secret = getattr(self.env, name, None)
                if secret:
                    detail = detail.replace(secret, "[redacted]")
            logger.error("Room receiver unavailable: %s", detail)
            return Response(
                "Receiver unavailable; restart bridge after recovery", status=503
            )


def observe_termination(task: asyncio.Future[Any]) -> None:
    # monitor has no abort API; observe its eventual settlement after idle shutdown.
    with suppress(asyncio.CancelledError, Exception):
        task.result()


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        if not configured(self.env):
            return Response("Configuration unavailable", status=503)
        if (
            js.URL.new(request.url).pathname != "/invocations"
            or request.method != "POST"
        ):
            return Response("Not found", status=404)
        actual = request.headers.get("Authorization", "").encode()
        expected = f"Bearer {self.env.BRIDGE_TOKEN}".encode()
        if not hmac.compare_digest(actual, expected):
            return Response("Unauthorized", status=401)
        body = await request.text()
        try:
            envelope = json.loads(body)
            if not isinstance(envelope, dict):
                raise ValueError("Expected an object")
        except ValueError:
            return Response("Invalid JSON envelope", status=400)
        if (
            envelope.get("agent_id") != self.env.BAND_AGENT_ID
            or not nonempty(envelope.get("room_id"))
            or not nonempty(envelope.get("event_type"))
        ):
            return Response("Invalid invocation routing", status=400)
        upstream = await self.env.ROOM.getByName(envelope["room_id"]).fetch(
            Request(request, body=body)
        )
        return tagged_response(
            upstream.js_object, {"x-band-worker-version": self.env.VERSION.id}
        )
