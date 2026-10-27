"""Workerd entry point that runs one scenario per route against the shipped Worker."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from contract import BAND_HOST, BODY, SETTINGS, Scenario
from kit import Routing, environment, recorded_logs, upstream_requests
from lifecycle import ROOM_SCENARIOS
from worker import (
    ANTHROPIC_HOST,
    CONTAINER_HOST,
    OPTIONAL_SETTINGS,
    PORT,
    PROXY_KEY,
    REQUIRED_SETTINGS,
    SECRET_SETTINGS,
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
        token = SETTINGS["BRIDGE_TOKEN"]
        arguments = {
            "method": case.get("method", "POST"),
            "headers": {"Authorization": case.get("auth", f"Bearer {token}")},
        }
        if arguments["method"] != "GET":
            arguments["body"] = case.get("body", BODY)
        with recorded_logs(logger) as logs:
            response = await worker.fetch(
                Request(
                    "https://worker" + case.get("path", "/invocations"), **arguments
                )
            )
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
        with upstream_requests() as upstream:
            allowed = await injector.fetch(
                Request(f"https://{BAND_HOST}/redirect", redirect="manual").js_object
            )
            other_host = await injector.fetch(
                Request("https://example.com/wrong").js_object
            )
            insecure = await injector.fetch(
                Request(f"http://{BAND_HOST}/wrong").js_object
            )
        return {
            "statuses": {
                "allowed": allowed.status,
                "other_host": other_host.status,
                "insecure": insecure.status,
            },
            "upstream": upstream,
        }


def constants() -> dict[str, Any]:
    return {
        "required": REQUIRED_SETTINGS,
        "optional": OPTIONAL_SETTINGS,
        "secret": SECRET_SETTINGS,
        "proxy_key": PROXY_KEY,
        "port": PORT,
        "anthropic_host": ANTHROPIC_HOST,
        "container_host": CONTAINER_HOST,
    }


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        return await self.env.HARNESS.getByName(urlsplit(request.url).path).fetch(
            request
        )
