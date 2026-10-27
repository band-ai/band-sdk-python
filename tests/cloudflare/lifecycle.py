"""Room lifecycle scenarios run inside workerd; each returns what the host asserts."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import js
from contract import CRASH, Scenario
from kit import (
    REFUSED,
    Clock,
    Container,
    environment,
    invocation,
    observed,
    outbound,
    recorded_logs,
    upstream_requests,
)
from worker import READINESS_SECONDS, Room, logger

Observations = dict[str, Any]


def room_for(ctx: Any, container: Container, settings: dict[str, Any]) -> Room:
    ctx.container = container
    return Room(ctx, environment(settings))


async def shared_boot(ctx: Any, settings: dict[str, Any]) -> Observations:
    container = Container()
    room = room_for(ctx, container, settings)
    container.admission.clear()
    async with asyncio.TaskGroup() as group:
        fetches = [group.create_task(room.fetch(invocation())) for _ in range(3)]
        await container.intercepting.wait()
        calls_while_booting = list(container.calls)
        container.admission.set()
    return {
        "calls_while_booting": calls_while_booting,
        "calls": container.calls,
        "responses": [await observed(fetch.result()) for fetch in fetches],
    }


async def warm_reuse(ctx: Any, settings: dict[str, Any]) -> Observations:
    container = Container()
    room = room_for(ctx, container, settings)
    first = await room.fetch(invocation())
    second = await room.fetch(invocation())
    return {
        "calls": container.calls,
        "idle_timeouts": container.idle_timeouts,
        "statuses": [first.status, second.status],
    }


async def ping_retry(ctx: Any, settings: dict[str, Any]) -> Observations:
    container = Container()
    room = room_for(ctx, container, settings)
    container.unready_answers = [REFUSED, 503]
    response = await room.fetch(invocation())
    return {"calls": container.calls, "status": response.status}


async def rehydration(ctx: Any, settings: dict[str, Any]) -> Observations:
    container = Container(running=True)
    room = room_for(ctx, container, settings)
    await container.idle_applied.wait()
    response = await room.fetch(invocation())
    return {
        "calls": container.calls,
        "idle_timeouts": container.idle_timeouts,
        "status": response.status,
    }


async def credentials(ctx: Any, settings: dict[str, Any]) -> Observations:
    container = Container()
    room = room_for(ctx, container, settings)
    response = await room.fetch(invocation())
    with upstream_requests() as upstream:
        for host, injector in container.injectors.items():
            await injector.fetch(outbound(f"https://{host}/probe"))
    options = json.loads(js.JSON.stringify(container.options))
    return {
        "status": response.status,
        "options": options,
        "keys_by_host": {
            urlsplit(sent["url"]).hostname: sent["key"] for sent in upstream
        },
        "forwarded": container.forwarded,
    }


async def crash_during_boot(ctx: Any, settings: dict[str, Any]) -> Observations:
    container = Container()
    room = room_for(ctx, container, settings)
    container.ping_ready.clear()
    clock = Clock()
    with clock.installed(asyncio.get_running_loop()):
        async with asyncio.TaskGroup() as group:
            fetches = [group.create_task(room.fetch(invocation())) for _ in range(3)]
            await container.ping_entered.wait()
            container.exit(js.Error.new(CRASH))
        container.ping_ready.set()
        recovered = await room.fetch(invocation())
    return {
        "statuses": [fetch.result().status for fetch in fetches],
        "recovered": recovered.status,
        "calls": container.calls,
        "deadline_timers_cancelled": [timer.cancelled for timer in clock.timers],
    }


async def stop_during_boot(ctx: Any, settings: dict[str, Any]) -> Observations:
    container = Container()
    room = room_for(ctx, container, settings)
    container.ping_ready.clear()
    async with asyncio.TaskGroup() as group:
        fetch = group.create_task(room.fetch(invocation()))
        await container.ping_entered.wait()
        container.exit()
    return {"status": fetch.result().status, "calls": container.calls}


async def expire_readiness(room: Room, container: Container) -> Observations:
    container.ping_ready.clear()
    container.ping_entered.clear()
    clock = Clock()
    with clock.installed(asyncio.get_running_loop()), recorded_logs(logger) as logs:
        async with asyncio.TaskGroup() as group:
            fetch = group.create_task(room.fetch(invocation()))
            await container.ping_entered.wait()
            clock.advance(READINESS_SECONDS - 1)
            fired_early = [timer.fired for timer in clock.timers]
            clock.advance(1)
    return {
        "status": fetch.result().status,
        "calls": container.calls,
        "running": container.running,
        "fired_early": fired_early,
        "deadline_timers_cancelled": [timer.cancelled for timer in clock.timers],
        "logs": logs,
    }


async def deadline(ctx: Any, settings: dict[str, Any]) -> Observations:
    container = Container()
    return await expire_readiness(room_for(ctx, container, settings), container)


async def deadline_while_running(ctx: Any, settings: dict[str, Any]) -> Observations:
    container = Container(running=True)
    room = room_for(ctx, container, settings)
    await container.idle_applied.wait()
    return await expire_readiness(room, container)


async def cleanup_failure(ctx: Any, settings: dict[str, Any]) -> Observations:
    container = Container()
    room = room_for(ctx, container, settings)
    container.fail_destroy = True
    container.ping_ready.clear()
    with recorded_logs(logger) as logs:
        async with asyncio.TaskGroup() as group:
            fetch = group.create_task(room.fetch(invocation()))
            await container.ping_entered.wait()
            container.exit(js.Error.new(CRASH))
    return {"status": fetch.result().status, "calls": container.calls, "logs": logs}


async def crash_after_ready(ctx: Any, settings: dict[str, Any]) -> Observations:
    container = Container()
    room = room_for(ctx, container, settings)
    with recorded_logs(logger) as logs:
        first = await room.fetch(invocation())
        run = container.run
        container.exit(js.Error.new(CRASH))
        await asyncio.gather(asyncio.ensure_future(run.promise), return_exceptions=True)
        second = await room.fetch(invocation())
    return {
        "statuses": [first.status, second.status],
        "calls": container.calls,
        "logs": logs,
    }


async def forward_failure(ctx: Any, settings: dict[str, Any]) -> Observations:
    container = Container()
    room = room_for(ctx, container, settings)
    container.fail_forward = True
    with recorded_logs(logger) as logs:
        response = await room.fetch(invocation())
    return {
        "response": await observed(response),
        "calls": container.calls,
        "running": container.running,
        "logs": logs,
    }


async def retries_share_monitor(ctx: Any, settings: dict[str, Any]) -> Observations:
    container = Container(running=True)
    room = room_for(ctx, container, settings)
    await container.idle_applied.wait()
    statuses = [(await expire_readiness(room, container))["status"] for _ in range(3)]
    container.ping_ready.set()
    with recorded_logs(logger) as logs:
        container.exit(js.Error.new(CRASH))
        recovered = await room.fetch(invocation())
    return {
        "statuses": statuses,
        "recovered": recovered.status,
        "calls": container.calls,
        "logs": logs,
    }


async def unreported_image(ctx: Any, settings: dict[str, Any]) -> Observations:
    container = Container(image=None)
    room = room_for(ctx, container, settings)
    return await observed(await room.fetch(invocation()))


ROOM_SCENARIOS: dict[
    Scenario, Callable[[Any, dict[str, Any]], Awaitable[Observations]]
] = {
    Scenario.SHARED_BOOT: shared_boot,
    Scenario.WARM_REUSE: warm_reuse,
    Scenario.PING_RETRY: ping_retry,
    Scenario.REHYDRATION: rehydration,
    Scenario.CREDENTIALS: credentials,
    Scenario.CRASH_DURING_BOOT: crash_during_boot,
    Scenario.STOP_DURING_BOOT: stop_during_boot,
    Scenario.DEADLINE: deadline,
    Scenario.DEADLINE_WHILE_RUNNING: deadline_while_running,
    Scenario.CLEANUP_FAILURE: cleanup_failure,
    Scenario.CRASH_AFTER_READY: crash_after_ready,
    Scenario.FORWARD_FAILURE: forward_failure,
    Scenario.RETRIES_SHARE_MONITOR: retries_share_monitor,
    Scenario.UNREPORTED_IMAGE: unreported_image,
}
