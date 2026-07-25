#!/usr/bin/env python3
"""Run real examples through the repository's live baseline E2E boundaries."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

STEP_TEMPLATE_VALUES = {"marker": "MARKER", "room_id": "room-id"}
PROCESS_TEMPLATE_VALUES = {
    "repo": "/repo",
    "path": "/repo/example.py",
    "workdir": "/tmp/run",
}
COLLABORATION_TEMPLATE_VALUES = {
    "marker": "MARKER",
    "source_id": "source-id",
    "source_name": "source-name",
    "target_id": "target-id",
    "target_name": "target-name",
}


@dataclass(frozen=True)
class Step:
    prompt: str
    barrier: str = "reply"
    contains_any: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    tool_calls_at_least: int = 0


@dataclass(frozen=True)
class ExampleSpec:
    id: str
    path: Path
    config_key: str
    command: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    unset_env: tuple[str, ...] = ()
    steps: tuple[Step, ...] = ()


@dataclass(frozen=True)
class Collaboration:
    source: str
    target: str
    prompt: str
    contains_any: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    examples: tuple[ExampleSpec, ...]
    collaborations: tuple[Collaboration, ...]


@dataclass
class Result:
    scenario: str
    example: str
    status: str
    detail: str = ""


@dataclass
class RunningExample:
    spec: ExampleSpec
    agent: Any
    process: asyncio.subprocess.Process
    workdir: tempfile.TemporaryDirectory[str]


def record_result(results: list[Result], result: Result) -> None:
    """Persist and immediately expose a completed scenario result."""
    results.append(result)
    detail = f" — {result.detail}" if result.detail else ""
    print(
        f"{result.status.upper()} {result.scenario} {result.example}{detail}",
        flush=True,
    )


def strings(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return tuple(value)


def required_string(raw: dict[str, Any], field: str, label: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label}.{field} must be a non-empty string")
    return value


def validate_template(value: str, field_name: str, allowed: dict[str, str]) -> None:
    try:
        value.format(**allowed)
    except (KeyError, ValueError) as error:
        raise ValueError(f"invalid {field_name} template: {error}") from error


def parse_step(raw: Any, label: str) -> Step:
    if not isinstance(raw, dict) or not isinstance(raw.get("prompt"), str):
        raise ValueError(f"{label} requires prompt")
    barrier = raw.get("barrier", "reply")
    if barrier not in {"reply", "processed"}:
        raise ValueError(f"unsupported barrier: {barrier}")
    validate_template(raw["prompt"], "step prompt", STEP_TEMPLATE_VALUES)
    contains_any = strings(raw.get("contains_any"), "contains_any")
    for value in contains_any:
        validate_template(value, "contains_any", STEP_TEMPLATE_VALUES)
    minimum = raw.get("tool_calls_at_least", 0)
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 0:
        raise ValueError("tool_calls_at_least must be a non-negative integer")
    return Step(
        prompt=raw["prompt"],
        barrier=barrier,
        contains_any=contains_any,
        tools=strings(raw.get("tools"), "tools"),
        tool_calls_at_least=minimum,
    )


def parse_steps(raw: Any, label: str) -> tuple[Step, ...]:
    if not isinstance(raw, list):
        raise ValueError(f"{label} must be a list")
    return tuple(
        parse_step(item, f"{label}[{index}]") for index, item in enumerate(raw)
    )


def resolve_example_path(repo: Path, relative_path: str) -> Path:
    path = (repo / relative_path).resolve()
    if not path.is_relative_to(repo.resolve()) or not path.is_file():
        raise ValueError(
            f"example path does not exist inside the repository: {relative_path}"
        )
    return path


def parse_environment(raw: Any, label: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in raw.items()
    ):
        raise ValueError(f"{label} must map strings to strings")
    return tuple(raw.items())


def validate_process_templates(spec: ExampleSpec) -> None:
    for value in spec.command:
        validate_template(value, "command", PROCESS_TEMPLATE_VALUES)
    for _, value in spec.environment:
        validate_template(value, "environment", PROCESS_TEMPLATE_VALUES)


def parse_example(raw: Any, index: int, repo: Path) -> ExampleSpec:
    label = f"examples[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a mapping")
    example_id = required_string(raw, "id", label)
    config_key = required_string(raw, "config_key", label)
    relative_path = required_string(raw, "path", label)
    spec = ExampleSpec(
        id=example_id,
        path=resolve_example_path(repo, relative_path),
        config_key=config_key,
        command=strings(raw.get("command"), "command"),
        environment=parse_environment(raw.get("env", {}), f"{label}.env"),
        unset_env=strings(raw.get("unset_env"), "unset_env"),
        steps=parse_steps(raw.get("steps", []), f"{label}.steps"),
    )
    validate_process_templates(spec)
    return spec


def parse_collaboration(raw: Any, index: int, ids: set[str]) -> Collaboration:
    label = f"collaborations[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a mapping")
    source = required_string(raw, "source", label)
    target = required_string(raw, "target", label)
    prompt = required_string(raw, "prompt", label)
    if source not in ids or target not in ids:
        raise ValueError(f"{label} requires known source and target")
    validate_template(prompt, "collaboration prompt", COLLABORATION_TEMPLATE_VALUES)
    contains_any = strings(raw.get("contains_any"), "contains_any")
    for value in contains_any:
        validate_template(value, "contains_any", COLLABORATION_TEMPLATE_VALUES)
    return Collaboration(source, target, prompt, contains_any)


def load_plan(path: Path, repo: Path) -> Plan:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if raw.get("version") != 1:
        raise ValueError("plan.version must be 1")
    if "topologies" in raw:
        raise ValueError(
            "topologies is not configurable; the runner always runs independent and together"
        )
    raw_examples = raw.get("examples")
    if not isinstance(raw_examples, list) or not raw_examples:
        raise ValueError("plan.examples must be a non-empty list")
    examples = tuple(
        parse_example(item, index, repo) for index, item in enumerate(raw_examples)
    )
    ids = [example.id for example in examples]
    if len(ids) != len(set(ids)):
        raise ValueError("example ids must be unique")
    raw_collaborations = raw.get("collaborations", [])
    if not isinstance(raw_collaborations, list):
        raise ValueError("collaborations must be a list")
    collaborations = tuple(
        parse_collaboration(item, index, set(ids))
        for index, item in enumerate(raw_collaborations)
    )
    return Plan(examples, collaborations)


def format_value(
    value: str,
    *,
    marker: str,
    room_id: str | None = None,
    source: RunningExample | None = None,
    target: RunningExample | None = None,
) -> str:
    values = {"marker": marker}
    if room_id is not None:
        values["room_id"] = room_id
    if source is not None:
        values.update(source_id=source.agent.id, source_name=source.agent.name)
    if target is not None:
        values.update(target_id=target.agent.id, target_name=target.agent.name)
    return value.format(**values)


def reply_capture_context(*args: Any, **kwargs: Any) -> Any:
    from tests.e2e.baseline.toolkit.capture import reply_capture

    return reply_capture(*args, **kwargs)


def process_values(spec: ExampleSpec, repo: Path, workdir: str) -> dict[str, str]:
    return {"repo": str(repo), "path": str(spec.path), "workdir": workdir}


def write_agent_config(spec: ExampleSpec, agent: Any, workdir: str) -> None:
    path = Path(workdir) / "agent_config.yaml"
    path.write_text(
        yaml.safe_dump(
            {spec.config_key: {"agent_id": agent.id, "api_key": agent.api_key}}
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def example_command(spec: ExampleSpec, repo: Path, workdir: str) -> tuple[str, ...]:
    values = process_values(spec, repo, workdir)
    return tuple(part.format(**values) for part in spec.command) or (
        sys.executable,
        str(spec.path),
    )


def example_environment(
    spec: ExampleSpec, repo: Path, workdir: str, settings: Any
) -> dict[str, str]:
    values = process_values(spec, repo, workdir)
    environment = os.environ.copy()
    environment.update(
        BAND_REST_URL=settings.endpoints.rest_url,
        BAND_WS_URL=settings.endpoints.ws_url,
        PYTHONPATH=os.pathsep.join(
            filter(None, (str(repo), environment.get("PYTHONPATH", "")))
        ),
    )
    for name in spec.unset_env:
        environment.pop(name, None)
    environment.update(
        {name: value.format(**values) for name, value in spec.environment}
    )
    return environment


async def start_example(
    spec: ExampleSpec, agent: Any, repo: Path, settings: Any
) -> RunningExample:
    workdir = tempfile.TemporaryDirectory(prefix=f"band-example-{spec.id}-")
    write_agent_config(spec, agent, workdir.name)
    try:
        process = await asyncio.create_subprocess_exec(
            *example_command(spec, repo, workdir.name),
            cwd=workdir.name,
            env=example_environment(spec, repo, workdir.name, settings),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
    except BaseException:
        workdir.cleanup()
        raise
    running = RunningExample(spec=spec, agent=agent, process=process, workdir=workdir)
    await asyncio.sleep(0)
    if process.returncode is not None:
        await stop_example(running)
        raise RuntimeError(f"{spec.id} exited during startup")
    return running


async def wait_or_exit(running: RunningExample, awaitable: Any) -> Any:
    boundary = asyncio.create_task(awaitable)
    exited = asyncio.create_task(running.process.wait())
    done, pending = await asyncio.wait(
        {boundary, exited}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    if exited in done:
        boundary.cancel()
        await asyncio.gather(boundary, return_exceptions=True)
        raise RuntimeError(f"{running.spec.id} exited with status {exited.result()}")
    return boundary.result()


async def stop_example(running: RunningExample) -> None:
    process = running.process
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=8)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=4)
            except TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
    running.workdir.cleanup()


def assert_contains(messages: Any, expected: tuple[str, ...]) -> None:
    if expected:
        messages.assert_contains_any(*expected)


async def wait_for_step(
    step: Step,
    running: RunningExample,
    capture: Any,
    message_id: str,
    cursor: Any,
    marker: str,
    room_id: str,
) -> None:
    if step.barrier == "processed":
        await wait_or_exit(
            running, capture.wait_for_processed(message_id, running.agent.id)
        )
        return
    replies = await wait_or_exit(
        running,
        capture.wait_for_reply(message_id, running.agent.id, since=cursor),
    )
    expected = tuple(
        format_value(value, marker=marker, room_id=room_id)
        for value in step.contains_any
    )
    assert_contains(replies, expected)


async def assert_step_tools(
    step: Step, running: RunningExample, capture: Any, since: datetime
) -> None:
    if not step.tools and not step.tool_calls_at_least:
        return
    calls = await capture.tool_calls(
        sender_id=running.agent.id,
        since=since,
        include_memory=True,
    )
    for tool in step.tools:
        calls.assert_fired(tool)
    if len(calls) < step.tool_calls_at_least:
        raise AssertionError(
            f"expected at least {step.tool_calls_at_least} tool call(s), "
            f"observed {len(calls)}"
        )


async def exercise_step(
    step: Step,
    running: RunningExample,
    resources: Any,
    capture: Any,
    room_id: str,
) -> None:
    marker = f"HUNT-{uuid.uuid4().hex[:10]}"
    cursor = capture.messages.snapshot()
    prompt = format_value(step.prompt, marker=marker, room_id=room_id)
    since = datetime.now(timezone.utc)
    message_id = await resources.user_ops.send_message(
        room_id,
        prompt,
        mention_id=running.agent.id,
        mention_name=running.agent.name,
    )
    await wait_for_step(step, running, capture, message_id, cursor, marker, room_id)
    await assert_step_tools(step, running, capture, since)


async def exercise_steps(
    running: RunningExample,
    resources: Any,
    ws: Any,
    settings: Any,
    scenario: str,
    results: list[Result],
) -> None:
    room_id = await resources.provision_room(
        title=f"example-hunt-{scenario}-{running.spec.id}",
        participants=[running.agent.id],
    )
    async with reply_capture_context(
        ws,
        room_id,
        user_ops=resources.user_ops,
        settings=settings,
        deadline_s=settings.e2e_timeout,
    ) as capture:
        steps = running.spec.steps or (
            Step("Reply with the exact marker {marker}.", contains_any=("{marker}",)),
        )
        for index, step in enumerate(steps, 1):
            await exercise_step(step, running, resources, capture, room_id)
            record_result(
                results, Result(scenario, running.spec.id, "pass", f"step {index}")
            )


async def exercise_steps_reported(
    running: RunningExample,
    resources: Any,
    ws: Any,
    settings: Any,
    scenario: str,
    results: list[Result],
) -> None:
    try:
        await exercise_steps(running, resources, ws, settings, scenario, results)
    except Exception as error:
        record_result(
            results,
            Result(scenario, running.spec.id, "fail", f"steps: {error}"),
        )


async def exercise_collaboration(
    collaboration: Collaboration,
    running: dict[str, RunningExample],
    resources: Any,
    ws: Any,
    settings: Any,
    results: list[Result],
) -> None:
    source = running[collaboration.source]
    target = running[collaboration.target]
    room_id = await resources.provision_room(
        title=f"example-hunt-collaboration-{source.spec.id}-{target.spec.id}",
        participants=[source.agent.id, target.agent.id],
    )
    async with reply_capture_context(
        ws,
        room_id,
        user_ops=resources.user_ops,
        settings=settings,
        deadline_s=settings.e2e_timeout,
    ) as capture:
        marker = f"HUNT-{uuid.uuid4().hex[:10]}"
        cursor = capture.messages.snapshot()
        prompt = format_value(
            collaboration.prompt, marker=marker, source=source, target=target
        )
        message_id = await resources.user_ops.send_message(
            room_id, prompt, mention_id=source.agent.id, mention_name=source.agent.name
        )
        await wait_or_exit(
            source, capture.wait_for_processed(message_id, source.agent.id)
        )
        expected = tuple(
            format_value(value, marker=marker, source=source, target=target)
            for value in collaboration.contains_any
        )

        def target_replied(messages: Any) -> bool:
            replies = messages.since(cursor).from_sender(target.agent.id)
            if not replies:
                return False
            return not expected or any(
                option.casefold() in message.content.casefold()
                for option in expected
                for message in replies
            )

        await wait_or_exit(target, capture.wait_until(target_replied))
        record_result(
            results,
            Result("collaboration", f"{source.spec.id}->{target.spec.id}", "pass"),
        )


async def start_group_examples(
    plan: Plan,
    resources: Any,
    settings: Any,
    repo: Path,
    results: list[Result],
) -> dict[str, RunningExample]:
    running: dict[str, RunningExample] = {}
    for spec in plan.examples:
        try:
            agent = await resources.provision_agent(f"group-{spec.id}")
            running[spec.id] = await start_example(spec, agent, repo, settings)
        except Exception as error:
            record_result(
                results,
                Result("together", spec.id, "fail", f"startup: {error}"),
            )
    return running


async def exercise_group_steps(
    running: dict[str, RunningExample],
    resources: Any,
    ws: Any,
    settings: Any,
    results: list[Result],
) -> None:
    async with asyncio.TaskGroup() as group:
        for item in running.values():
            group.create_task(
                exercise_steps_reported(
                    item, resources, ws, settings, "together", results
                )
            )


async def exercise_shared_turn(
    running: RunningExample,
    resources: Any,
    capture: Any,
    room_id: str,
) -> None:
    marker = f"HUNT-{uuid.uuid4().hex[:10]}"
    cursor = capture.messages.snapshot()
    message_id = await resources.user_ops.send_message(
        room_id,
        f"Reply with the exact marker {marker}.",
        mention_id=running.agent.id,
        mention_name=running.agent.name,
    )
    replies = await wait_or_exit(
        running,
        capture.wait_for_reply(message_id, running.agent.id, since=cursor),
    )
    replies.assert_contains_any(marker)


async def exercise_shared_turn_reported(
    running: RunningExample,
    resources: Any,
    capture: Any,
    room_id: str,
    results: list[Result],
) -> None:
    try:
        await exercise_shared_turn(running, resources, capture, room_id)
        result = Result("shared-room", running.spec.id, "pass")
    except Exception as error:
        result = Result("shared-room", running.spec.id, "fail", str(error))
    record_result(results, result)


def record_shared_setup_failure(
    running: dict[str, RunningExample], results: list[Result], error: Exception
) -> None:
    for item in running.values():
        record_result(
            results,
            Result("shared-room", item.spec.id, "fail", f"setup: {error}"),
        )


async def exercise_shared_room(
    running: dict[str, RunningExample],
    resources: Any,
    ws: Any,
    settings: Any,
    results: list[Result],
) -> None:
    if not running:
        return
    try:
        room_id = await resources.provision_room(
            title="example-hunt-shared-room",
            participants=[item.agent.id for item in running.values()],
        )
    except Exception as error:
        record_shared_setup_failure(running, results, error)
        return

    try:
        async with reply_capture_context(
            ws,
            room_id,
            user_ops=resources.user_ops,
            settings=settings,
            deadline_s=settings.e2e_timeout,
        ) as capture:
            for item in running.values():
                await exercise_shared_turn_reported(
                    item, resources, capture, room_id, results
                )
    except Exception as error:
        record_result(
            results,
            Result("shared-room", "group", "fail", f"capture: {error}"),
        )


async def exercise_collaborations(
    collaborations: tuple[Collaboration, ...],
    running: dict[str, RunningExample],
    resources: Any,
    ws: Any,
    settings: Any,
    results: list[Result],
) -> None:
    for collaboration in collaborations:
        label = f"{collaboration.source}->{collaboration.target}"
        if collaboration.source not in running or collaboration.target not in running:
            record_result(
                results,
                Result(
                    "collaboration",
                    label,
                    "fail",
                    "participant failed to start",
                ),
            )
            continue
        try:
            await exercise_collaboration(
                collaboration, running, resources, ws, settings, results
            )
        except Exception as error:
            record_result(results, Result("collaboration", label, "fail", str(error)))


async def stop_examples(running: dict[str, RunningExample]) -> None:
    await asyncio.gather(
        *(stop_example(item) for item in running.values()), return_exceptions=True
    )


async def run_group(
    plan: Plan,
    resources: Any,
    ws: Any,
    settings: Any,
    repo: Path,
    results: list[Result],
) -> None:
    running = await start_group_examples(plan, resources, settings, repo, results)
    try:
        await exercise_group_steps(running, resources, ws, settings, results)
        await exercise_shared_room(running, resources, ws, settings, results)
        await exercise_collaborations(
            plan.collaborations, running, resources, ws, settings, results
        )
    finally:
        await stop_examples(running)


async def run_independent_example(
    spec: ExampleSpec,
    resources: Any,
    ws: Any,
    settings: Any,
    repo: Path,
    results: list[Result],
) -> None:
    running: RunningExample | None = None
    try:
        agent = await resources.provision_agent(f"solo-{spec.id}")
        running = await start_example(spec, agent, repo, settings)
        await exercise_steps(running, resources, ws, settings, "independent", results)
    except Exception as error:
        record_result(results, Result("independent", spec.id, "fail", str(error)))
    finally:
        if running is not None:
            await stop_example(running)


async def run_independent_examples(
    plan: Plan,
    resources: Any,
    ws: Any,
    settings: Any,
    repo: Path,
    results: list[Result],
) -> None:
    for spec in plan.examples:
        await run_independent_example(spec, resources, ws, settings, repo, results)


async def run_live(plan: Plan, repo: Path, keep: bool) -> list[Result]:
    sys.path.insert(0, str(repo))
    from tests.e2e.baseline.settings import BaselineSettings
    from tests.e2e.baseline.toolkit.provisioning import (
        ResourceManager,
        user_rest_client,
    )
    from tests.e2e.baseline.toolkit.ws import user_ws_observer

    settings = BaselineSettings()
    if not settings.e2e_tests_enabled:
        raise ValueError("E2E_TESTS_ENABLED must be true")
    if not settings.credentials.api_key_user:
        raise ValueError("BAND_API_KEY_USER is required")
    resources = ResourceManager(
        user_client=user_rest_client(settings),
        settings=settings,
        run_id=f"hunt-{uuid.uuid4().hex[:8]}",
    )
    results: list[Result] = []
    try:
        async with user_ws_observer(settings) as ws:
            await run_independent_examples(plan, resources, ws, settings, repo, results)
            try:
                await run_group(plan, resources, ws, settings, repo, results)
            except Exception as error:
                record_result(results, Result("together", "group", "fail", str(error)))
    finally:
        if not keep:
            await resources.reap_all()
    return results


def parser() -> argparse.ArgumentParser:
    default_repo = Path(__file__).resolve().parents[4]
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("plan", type=Path)
    result.add_argument("--repo", type=Path, default=default_repo)
    result.add_argument("--dry-run", action="store_true")
    result.add_argument(
        "--keep", action="store_true", help="Keep provisioned rooms and agents"
    )
    result.add_argument("--json-out", type=Path)
    return result


def main() -> None:
    args = parser().parse_args()
    repo = args.repo.resolve()
    plan = load_plan(args.plan, repo)
    if args.dry_run:
        print(
            f"valid plan: {len(plan.examples)} examples; "
            "topologies=independent,together"
        )
        return
    results = asyncio.run(run_live(plan, repo, args.keep))
    payload = [result.__dict__ for result in results]
    if args.json_out:
        args.json_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    passed = sum(result.status == "pass" for result in results)
    failed = sum(result.status == "fail" for result in results)
    print(f"SUMMARY passed={passed} failed={failed}")
    if any(result.status == "fail" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
