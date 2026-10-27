"""Workerd entry point that runs one scenario per route against the shipped Worker."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from contract import BAND_HOST, SETTINGS, Scenario
from kit import (
    Routing,
    environment,
    invocation,
    outbound,
    recorded_logs,
    upstream_requests,
)
from lifecycle import ROOM_SCENARIOS
from worker import (
    ANTHROPIC_HOST,
    CLEANUP_FAILED_LOG,
    COLD_START_LOG,
    CONFIGURATION_UNAVAILABLE,
    CONTAINER_HOST,
    CONTAINER_INSTANCE,
    FORBIDDEN_UPSTREAM,
    HTTP,
    IDLE_MS,
    INVALID_ENVELOPE,
    INVALID_ENVELOPE_LOG,
    INVALID_ROUTING,
    INVALID_ROUTING_LOG,
    INVALID_SETTINGS_LOG,
    INVOCATIONS_PATH,
    NOT_FOUND,
    OPTIONAL_SETTINGS,
    PORT,
    PORT_SETTING,
    PROXY_KEY,
    READINESS_EXCEEDED,
    READY_LOG,
    REATTACH_LOG,
    RECEIVER_FAILED_LOG,
    RECEIVER_STOPPED_LOG,
    RECEIVER_UNAVAILABLE,
    RECEIVER_UNAVAILABLE_LOG,
    REDACTED,
    REQUIRED_SETTINGS,
    SECRET_SETTINGS,
    UNAUTHORIZED,
    UNAUTHORIZED_LOG,
    UNKNOWN_IMAGE,
    WORKER_VERSION_HEADER,
    KeyInjector,
    logger,
    runtime_options,
)
from worker import Default as Ingress
from workers import DurableObject, Request, Response, WorkerEntrypoint

__all__ = ["Default", "Harness", "KeyInjector"]


class Harness(DurableObject):
    async def fetch(self, request: Request) -> Response:
        try:
            scenario = Scenario(urlsplit(request.url).path.strip("/"))
        except ValueError:
            return Response("Unknown scenario", status=404)
        payload = await request.json()
        match scenario:
            case Scenario.CONSTANTS:
                observations = constants()
            case Scenario.INGRESS:
                observations = await self.ingress(payload)
            case Scenario.KEY_INJECTOR:
                observations = await self.key_injector()
            case _:
                observations = await ROOM_SCENARIOS[scenario](
                    self.ctx, payload.get("settings", {})
                )
        return Response.from_json(observations)

    async def ingress(self, case: dict[str, Any]) -> dict[str, Any]:
        routing = Routing()
        worker = Ingress(self.ctx, environment(case.get("settings", {}), ROOM=routing))
        with recorded_logs(logger) as logs:
            response = await worker.fetch(invocation(**case.get("request", {})))
        return {
            "status": response.status,
            "body": await response.text(),
            "room": routing.room,
            "forwarded_body": routing.body,
            "worker_version": response.headers.get(WORKER_VERSION_HEADER),
            "logs": logs,
        }

    async def key_injector(self) -> dict[str, Any]:
        injector = self.ctx.exports.KeyInjector(
            runtime_options(
                {"props": {"host": BAND_HOST, "key": SETTINGS["BAND_API_KEY"]}}
            )
        )
        targets = {
            "allowed": f"https://{BAND_HOST}/redirect",
            "other_host": "https://example.com/wrong",
            "other_port": f"https://{BAND_HOST}:8443/wrong",
            "lookalike_host": f"https://{BAND_HOST}.example.com/wrong",
            "insecure": f"http://{BAND_HOST}/wrong",
        }
        with upstream_requests() as upstream:
            outcomes = {
                name: await answered(
                    await injector.fetch(outbound(url, redirect="manual"))
                )
                for name, url in targets.items()
            }
        return {"outcomes": outcomes, "upstream": upstream}


async def answered(response: Any) -> list[Any]:
    return [response.status, await response.text()]


def constants() -> dict[str, Any]:
    """Expose the Worker's own vocabulary so host assertions never retype it."""
    return {
        "required": REQUIRED_SETTINGS,
        "optional": OPTIONAL_SETTINGS,
        "secret": SECRET_SETTINGS,
        "proxy_key": PROXY_KEY,
        "port": PORT,
        "anthropic_host": ANTHROPIC_HOST,
        "container_host": CONTAINER_HOST,
        "instance": CONTAINER_INSTANCE,
        "idle_ms": IDLE_MS,
        "redacted": REDACTED,
        "invocations_path": INVOCATIONS_PATH,
        "unknown_image": UNKNOWN_IMAGE,
        "configuration_unavailable": CONFIGURATION_UNAVAILABLE,
        "not_found": NOT_FOUND,
        "forbidden_upstream": FORBIDDEN_UPSTREAM,
        "port_setting": PORT_SETTING,
        "http": HTTP,
        "unauthorized_log": UNAUTHORIZED_LOG,
        "invalid_envelope_log": INVALID_ENVELOPE_LOG,
        "invalid_routing_log": INVALID_ROUTING_LOG,
        "cold_start_log": COLD_START_LOG,
        "reattach_log": REATTACH_LOG,
        "ready_log": READY_LOG,
        "unauthorized": UNAUTHORIZED,
        "invalid_envelope": INVALID_ENVELOPE,
        "invalid_routing": INVALID_ROUTING,
        "receiver_unavailable": RECEIVER_UNAVAILABLE,
        "invalid_settings_log": INVALID_SETTINGS_LOG,
        "receiver_unavailable_log": RECEIVER_UNAVAILABLE_LOG,
        "receiver_failed_log": RECEIVER_FAILED_LOG,
        "cleanup_failed_log": CLEANUP_FAILED_LOG,
        "receiver_stopped_log": RECEIVER_STOPPED_LOG,
        "readiness_exceeded": READINESS_EXCEEDED,
    }


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        return await self.env.HARNESS.getByName(urlsplit(request.url).path).fetch(
            request
        )
