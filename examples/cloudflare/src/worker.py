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
from typing import Any

import js
from pyodide.ffi import JsException, create_once_callable, to_js
from workers import DurableObject, Request, Response, WorkerEntrypoint

logger = logging.getLogger(__name__)
PORT = 8080
CONTAINER_HOST = "container"
CONTAINER_INSTANCE = {"vcpu": 0.25, "memoryMib": 1024, "diskMb": 4000}
READINESS_SECONDS = 90
PING_INTERVAL_SECONDS = 0.25
IDLE_MS = 180_000
PROXY_KEY = "proxy-managed"
ANTHROPIC_HOST = "api.anthropic.com"
REST_URL_SETTING = "BAND_REST_URL"
SECRET_SETTINGS = ("BAND_API_KEY", "ANTHROPIC_API_KEY", "BRIDGE_TOKEN")
REQUIRED_SETTINGS = ("BAND_AGENT_ID", REST_URL_SETTING, *SECRET_SETTINGS)
OPTIONAL_SETTINGS = ("ANTHROPIC_MODEL",)
AUTHORIZATION = "Authorization"
WORKER_VERSION_HEADER = "x-band-worker-version"
ROOM_VERSION_HEADER = "x-band-room-version"
CONTAINER_IMAGE_HEADER = "x-band-container-image"
UNKNOWN_IMAGE = "unknown"
REDACTED = "[redacted]"
CONFIGURATION_UNAVAILABLE = "Configuration unavailable"
RECEIVER_UNAVAILABLE = "Receiver unavailable; restart bridge after recovery"
INVALID_SETTINGS_LOG = "Worker settings invalid: %s"
RECEIVER_UNAVAILABLE_LOG = "Room receiver unavailable: %s"
RECEIVER_FAILED_LOG = "Room receiver failed: %s"
CLEANUP_FAILED_LOG = "Room receiver cleanup failed: %s"


def runtime_options(value: dict[str, Any]) -> Any:
    """Convert nested options for Cloudflare's native runtime APIs."""
    return to_js(value, dict_converter=js.Object.fromEntries)


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def trimmed(value: Any) -> bool:
    """Accept a non-empty string without surrounding whitespace."""
    return nonempty(value) and value == value.strip()


def secure_endpoint(url: str) -> bool:
    try:
        endpoint = js.URL.new(url)
    except JsException:
        return False
    return bool(
        endpoint.protocol == "https:"
        and endpoint.hostname
        and not endpoint.username
        and not endpoint.password
    )


def invalid_settings(env: Any) -> list[str]:
    """Name each setting that makes the Worker fail closed."""
    invalid = [
        name for name in REQUIRED_SETTINGS if not trimmed(getattr(env, name, None))
    ]
    invalid += [
        name
        for name in OPTIONAL_SETTINGS
        if getattr(env, name, None) and not trimmed(getattr(env, name))
    ]
    if REST_URL_SETTING not in invalid and not secure_endpoint(
        getattr(env, REST_URL_SETTING)
    ):
        invalid.append(REST_URL_SETTING)
    return invalid


def redacted(env: Any, detail: str) -> str:
    for name in SECRET_SETTINGS:
        secret = getattr(env, name, None)
        if secret:
            detail = detail.replace(secret, REDACTED)
    return detail


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
        self.boot: asyncio.Task[None] | None = None
        self.termination: asyncio.Future[Any] | None = None
        self.image = UNKNOWN_IMAGE
        if ctx.container and ctx.container.running:
            ctx.blockConcurrencyWhile(create_once_callable(self.restore_idle))

    async def restore_idle(self) -> None:
        await self.ctx.container.setInactivityTimeout(IDLE_MS)

    async def ensure_ready(self) -> None:
        if self.boot is None or not self.serving(self.boot):
            self.boot = asyncio.create_task(self.start_and_wait())
        await asyncio.shield(self.boot)

    def serving(self, boot: asyncio.Task[None]) -> bool:
        """Reuse a boot while it runs, or after it succeeded while its container runs."""
        if not boot.done():
            return True
        return (
            not boot.cancelled()
            and boot.exception() is None
            and self.ctx.container.running
        )

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
            termination = self.watch(container, new_run=starting)
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
                await self.discard(container)
                if termination is not None:
                    await asyncio.gather(termination, return_exceptions=True)
            raise
        finally:
            abort.abort()
        inspection = await container.inspect()
        self.image = (inspection and inspection.image) or UNKNOWN_IMAGE

    def watch(self, container: Any, *, new_run: bool) -> asyncio.Future[Any]:
        """Monitor each container run once, however many boots wait on it."""
        if new_run or self.termination is None or self.termination.done():
            self.termination = asyncio.ensure_future(container.monitor())
            self.termination.add_done_callback(self.receiver_stopped)
        return self.termination

    def proxied_keys(self) -> dict[str, str]:
        """Map each upstream host to the key setting the Worker injects for it."""
        band_host = js.URL.new(getattr(self.env, REST_URL_SETTING)).host
        return {band_host: "BAND_API_KEY", ANTHROPIC_HOST: "ANTHROPIC_API_KEY"}

    async def start(self, container: Any) -> None:
        proxied = self.proxied_keys()
        for host, setting in proxied.items():
            key = getattr(self.env, setting)
            injector = self.ctx.exports.KeyInjector(
                runtime_options({"props": {"host": host, "key": key}})
            )
            await container.interceptOutboundHttps(host, injector)
        environment = (
            {
                "BAND_AGENT_ID": self.env.BAND_AGENT_ID,
                REST_URL_SETTING: getattr(self.env, REST_URL_SETTING),
                "PORT": str(PORT),
            }
            | dict.fromkeys(proxied.values(), PROXY_KEY)
            | {
                name: getattr(self.env, name)
                for name in OPTIONAL_SETTINGS
                if getattr(self.env, name, None)
            }
        )
        container.start(
            runtime_options(
                {
                    "image": container.images.base,
                    "instance": CONTAINER_INSTANCE,
                    "enableInternet": False,
                    "env": environment,
                }
            )
        )
        await container.setInactivityTimeout(IDLE_MS)

    async def discard(self, container: Any) -> None:
        # Cleanup failure must not replace the startup error the caller reports.
        try:
            await container.destroy()
        except JsException as error:
            logger.warning(CLEANUP_FAILED_LOG, redacted(self.env, str(error)))

    def receiver_stopped(self, termination: asyncio.Future[Any]) -> None:
        if termination.cancelled():
            return
        if error := termination.exception():
            logger.warning(RECEIVER_FAILED_LOG, redacted(self.env, str(error)))
        else:
            logger.info("Room receiver stopped")

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
                    f"http://{CONTAINER_HOST}/ping",
                    runtime_options({"signal": signals}),
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
                await asyncio.sleep(min(PING_INTERVAL_SECONDS, remaining))
        raise RuntimeError("Receiver readiness deadline exceeded")

    async def fetch(self, request: Request) -> Response:
        try:
            await self.ensure_ready()
            target = js.URL.new(request.url)
            target.protocol = "http:"
            target.hostname = CONTAINER_HOST
            target.port = ""
            forwarded = js.Request.new(target, request.js_object)
            forwarded.headers.delete(AUTHORIZATION)
            upstream = await self.ctx.container.getTcpPort(PORT).fetch(forwarded)
            return tagged_response(
                upstream,
                {
                    ROOM_VERSION_HEADER: self.env.VERSION.id,
                    CONTAINER_IMAGE_HEADER: self.image,
                },
            )
        except (JsException, RuntimeError, ValueError) as error:
            logger.error(RECEIVER_UNAVAILABLE_LOG, redacted(self.env, str(error)))
            return Response(RECEIVER_UNAVAILABLE, status=503)


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        if invalid := invalid_settings(self.env):
            logger.error(INVALID_SETTINGS_LOG, ", ".join(invalid))
            return Response(CONFIGURATION_UNAVAILABLE, status=503)
        # workers' Request.method raises KeyError outside http.HTTPMethod.
        if (
            js.URL.new(request.url).pathname != "/invocations"
            or request.js_object.method != "POST"
        ):
            return Response("Not found", status=404)
        actual = request.headers.get(AUTHORIZATION, "").encode()
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
            upstream.js_object, {WORKER_VERSION_HEADER: self.env.VERSION.id}
        )
