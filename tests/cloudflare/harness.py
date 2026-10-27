"""Exercise the shipped Worker with only external container/HTTP boundaries doubled."""

from __future__ import annotations

import asyncio
import io
import logging
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import js
from kit import BODY, IMAGE, SETTINGS, Clock, Container, Routing
from pyodide.ffi import create_proxy
from worker import (
    READINESS_SECONDS,
    SECRET_SETTINGS,
    KeyInjector,
    Room,
    logger,
    runtime_options,
)
from worker import Default as Ingress
from workers import DurableObject, Request, Response, WorkerEntrypoint

__all__ = ["Default", "Harness", "KeyInjector"]


class Harness(DurableObject):
    async def fetch(self, request: Request) -> Response:
        scenario = urlsplit(request.url).path.rsplit("/", 1)[-1]
        if scenario == "ingress":
            return await self.ingress(await request.json())
        if scenario == "interceptor":
            return await self.interceptor()
        return await self.lifecycle(scenario)

    async def ingress(self, case: dict[str, Any]) -> Response:
        routing = Routing()
        values = {**SETTINGS, "ROOM": routing, **case.get("settings", {})}
        for name in case.get("missing", []):
            values.pop(name)
        worker = Ingress(self.ctx, SimpleNamespace(**values))
        arguments = {
            "method": case.get("method", "POST"),
            "headers": {"Authorization": case.get("auth", "Bearer private-bridge")},
        }
        if arguments["method"] != "GET":
            arguments["body"] = case["body"]
        response = await worker.fetch(
            Request("https://worker" + case.get("path", "/invocations"), **arguments)
        )
        return Response.from_json(
            {
                "status": response.status,
                "body": await response.text(),
                "room": routing.room,
                "forwarded_body": routing.body,
                "version": response.headers.get("x-band-worker-version"),
            }
        )

    async def interceptor(self) -> Response:
        captured = []
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
            injector = self.ctx.exports.KeyInjector(
                runtime_options({"props": {"host": "app.band.ai", "key": "test-key"}})
            )
            response = await injector.fetch(
                Request("https://app.band.ai/redirect", redirect="manual").js_object
            )
            denied = await injector.fetch(
                Request("https://example.com/wrong").js_object
            )
            insecure = await injector.fetch(
                Request("http://app.band.ai/wrong").js_object
            )
            assert response.status == 302
            assert denied.status == insecure.status == 403
            assert captured == [
                {
                    "url": "https://app.band.ai/redirect",
                    "key": "test-key",
                    "redirect": "manual",
                }
            ]
            return Response.from_json({"passed": True})
        finally:
            js.fetch = original
            proxy.destroy()

    async def lifecycle(self, scenario: str) -> Response:
        container = Container()
        js.Object.defineProperty(
            self.ctx._ctx,
            "container",
            runtime_options({"value": container, "configurable": True}),
        )
        room = Room(self.ctx, SimpleNamespace(**SETTINGS))
        tasks = [
            asyncio.create_task(
                room.fetch(
                    Request(
                        "https://public-worker/invocations",
                        method="POST",
                        body=BODY,
                        headers={"Authorization": "Bearer " + SETTINGS["BRIDGE_TOKEN"]},
                    )
                )
            )
            for _ in range(3)
        ]
        loop = asyncio.get_running_loop()
        original_time, original_call_at = loop.time, loop.call_at
        clock = Clock()
        if scenario == "deadline":
            loop.time, loop.call_at = clock.time, clock.call_at
        try:
            await container.ping_entered.wait()
            assert container.calls.count("start") == 1
            assert "forward" not in container.calls
            assert container.calls[:3] == [
                "intercept:app.band.ai",
                "intercept:api.anthropic.com",
                "start",
            ]
            if scenario == "crash":
                container.stopped.set_exception(
                    RuntimeError("Controlled startup crash")
                )
            elif scenario == "deadline":
                clock.advance(READINESS_SECONDS)
            else:
                container.release_ping.set()
            responses = await asyncio.gather(*tasks)
            if scenario in ("crash", "deadline"):
                assert [response.status for response in responses] == [503] * 3
                assert container.aborted
                assert container.calls.count("destroy") == 1
                assert not container.running
                assert container.stopped.done()
                assert all(timer.cancelled for timer in clock.timers)
                # A failed shared boot must release ownership for the next attempt.
                container.stopped = loop.create_future()
                container.release_ping.set()
                recovered = await room.fetch(
                    Request("https://worker/invocations", method="POST", body=BODY)
                )
                assert recovered.status == 201
                assert container.calls.count("start") == 2
            else:
                assert [response.status for response in responses] == [201] * 3
                assert [await response.text() for response in responses] == [BODY] * 3
                assert all(
                    response.headers.get("x-band-container-image") == IMAGE
                    for response in responses
                )
                assert all(
                    response.headers.get("x-band-room-version") == "version"
                    for response in responses
                )
                container.restored.clear()
                Room(self.ctx, SimpleNamespace(**SETTINGS))
                await container.restored.wait()
                assert container.calls.count("start") == 1
                container.fail_forward = True
                output = io.StringIO()
                handler = logging.StreamHandler(output)
                logger.addHandler(handler)
                try:
                    failed = await room.fetch(
                        Request("https://worker/invocations", method="POST", body=BODY)
                    )
                finally:
                    logger.removeHandler(handler)
                    handler.close()
                assert failed.status == 503
                assert container.running
                assert "destroy" not in container.calls
                assert output.getvalue()
                body = await failed.text()
                for name in SECRET_SETTINGS:
                    assert SETTINGS[name] not in output.getvalue()
                    assert SETTINGS[name] not in body

            return Response.from_json({"passed": True, "calls": container.calls})
        finally:
            loop.time, loop.call_at = original_time, original_call_at
            container.release_ping.set()
            await container.destroy()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(container.stopped, return_exceptions=True)


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        return await self.env.HARNESS.getByName(urlsplit(request.url).path).fetch(
            request
        )
