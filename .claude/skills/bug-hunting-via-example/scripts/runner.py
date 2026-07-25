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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Step:
    prompt: str
    barrier: str = "reply"
    contains_any: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExampleSpec:
    id: str
    path: Path
    config_key: str
    command: tuple[str, ...] = ()
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
    topologies: tuple[str, ...]
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
    output: list[str] = field(default_factory=list)
    pump: asyncio.Task[None] | None = None


def strings(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return tuple(value)


def validate_template(value: str, field_name: str, allowed: dict[str, str]) -> None:
    try:
        value.format(**allowed)
    except (KeyError, ValueError) as error:
        raise ValueError(f"invalid {field_name} template: {error}") from error


def load_plan(path: Path, repo: Path) -> Plan:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if raw.get("version") != 1:
        raise ValueError("plan.version must be 1")
    raw_examples = raw.get("examples")
    if not isinstance(raw_examples, list) or not raw_examples:
        raise ValueError("plan.examples must be a non-empty list")

    examples: list[ExampleSpec] = []
    ids: set[str] = set()
    for index, item in enumerate(raw_examples):
        if not isinstance(item, dict):
            raise ValueError(f"examples[{index}] must be a mapping")
        example_id = item.get("id")
        config_key = item.get("config_key")
        relative_path = item.get("path")
        if not all(
            isinstance(value, str) and value
            for value in (example_id, config_key, relative_path)
        ):
            raise ValueError(
                f"examples[{index}] requires non-empty id, path, and config_key"
            )
        if example_id in ids:
            raise ValueError(f"duplicate example id: {example_id}")
        ids.add(example_id)
        example_path = (repo / relative_path).resolve()
        if (
            not example_path.is_relative_to(repo.resolve())
            or not example_path.is_file()
        ):
            raise ValueError(
                f"example path does not exist inside the repository: {relative_path}"
            )
        raw_steps = item.get("steps", [])
        if not isinstance(raw_steps, list):
            raise ValueError(f"examples[{index}].steps must be a list")
        steps: list[Step] = []
        for step_index, raw_step in enumerate(raw_steps):
            if not isinstance(raw_step, dict) or not isinstance(
                raw_step.get("prompt"), str
            ):
                raise ValueError(
                    f"examples[{index}].steps[{step_index}] requires prompt"
                )
            barrier = raw_step.get("barrier", "reply")
            if barrier not in {"reply", "processed"}:
                raise ValueError(f"unsupported barrier: {barrier}")
            validate_template(raw_step["prompt"], "step prompt", {"marker": "MARKER"})
            for value in strings(raw_step.get("contains_any"), "contains_any"):
                validate_template(value, "contains_any", {"marker": "MARKER"})
            steps.append(
                Step(
                    prompt=raw_step["prompt"],
                    barrier=barrier,
                    contains_any=strings(raw_step.get("contains_any"), "contains_any"),
                )
            )
        examples.append(
            ExampleSpec(
                id=example_id,
                path=example_path,
                config_key=config_key,
                command=strings(item.get("command"), "command"),
                unset_env=strings(item.get("unset_env"), "unset_env"),
                steps=tuple(steps),
            )
        )
        for value in examples[-1].command:
            validate_template(
                value,
                "command",
                {"repo": "/repo", "path": "/repo/example.py", "workdir": "/tmp/run"},
            )

    topologies = strings(raw.get("topologies", ["independent"]), "topologies")
    if not topologies:
        raise ValueError("topologies must not be empty")
    invalid = set(topologies) - {"independent", "together"}
    if invalid:
        raise ValueError(f"unsupported topologies: {', '.join(sorted(invalid))}")
    raw_collaborations = raw.get("collaborations", [])
    if not isinstance(raw_collaborations, list):
        raise ValueError("collaborations must be a list")
    collaborations: list[Collaboration] = []
    for index, item in enumerate(raw_collaborations):
        if not isinstance(item, dict):
            raise ValueError(f"collaborations[{index}] must be a mapping")
        source, target, prompt = (
            item.get("source"),
            item.get("target"),
            item.get("prompt"),
        )
        if source not in ids or target not in ids or not isinstance(prompt, str):
            raise ValueError(
                f"collaborations[{index}] requires known source/target and prompt"
            )
        collaboration_values = {
            "marker": "MARKER",
            "source_id": "source-id",
            "source_name": "source-name",
            "target_id": "target-id",
            "target_name": "target-name",
        }
        validate_template(prompt, "collaboration prompt", collaboration_values)
        for value in strings(item.get("contains_any"), "contains_any"):
            validate_template(value, "contains_any", collaboration_values)
        collaborations.append(
            Collaboration(
                source=source,
                target=target,
                prompt=prompt,
                contains_any=strings(item.get("contains_any"), "contains_any"),
            )
        )
    if collaborations and "together" not in topologies:
        raise ValueError("collaborations require the together topology")
    return Plan(tuple(examples), topologies, tuple(collaborations))


def format_value(
    value: str,
    *,
    marker: str,
    source: RunningExample | None = None,
    target: RunningExample | None = None,
) -> str:
    values = {"marker": marker}
    if source is not None:
        values.update(source_id=source.agent.id, source_name=source.agent.name)
    if target is not None:
        values.update(target_id=target.agent.id, target_name=target.agent.name)
    return value.format(**values)


async def pump_output(running: RunningExample) -> None:
    assert running.process.stdout is not None
    while line := await running.process.stdout.readline():
        running.output.append(line.decode(errors="replace").rstrip())
        del running.output[:-80]


async def start_example(
    spec: ExampleSpec, agent: Any, repo: Path, settings: Any
) -> RunningExample:
    workdir = tempfile.TemporaryDirectory(prefix=f"band-example-{spec.id}-")
    config_path = Path(workdir.name) / "agent_config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {spec.config_key: {"agent_id": agent.id, "api_key": agent.api_key}}
        ),
        encoding="utf-8",
    )
    config_path.chmod(0o600)
    replacements = {"repo": str(repo), "path": str(spec.path), "workdir": workdir.name}
    command = tuple(part.format(**replacements) for part in spec.command) or (
        sys.executable,
        str(spec.path),
    )
    environment = os.environ.copy()
    environment["BAND_REST_URL"] = settings.endpoints.rest_url
    environment["BAND_WS_URL"] = settings.endpoints.ws_url
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(repo), environment.get("PYTHONPATH", "")))
    )
    for name in spec.unset_env:
        environment.pop(name, None)
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=workdir.name,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
    except BaseException:
        workdir.cleanup()
        raise
    running = RunningExample(spec=spec, agent=agent, process=process, workdir=workdir)
    running.pump = asyncio.create_task(pump_output(running))
    await asyncio.sleep(0)
    if process.returncode is not None:
        await stop_example(running)
        raise RuntimeError(f"{spec.id} exited during startup")
    return running


def output_tail(running: RunningExample) -> str:
    tail = "\n".join(running.output[-12:]).replace(running.agent.api_key, "[REDACTED]")
    return f"; last output:\n{tail}" if tail else ""


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
        raise RuntimeError(
            f"{running.spec.id} exited with status {exited.result()}"
            f"{output_tail(running)}"
        )
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
    if running.pump is not None:
        await running.pump
    running.workdir.cleanup()


def assert_contains(messages: Any, expected: tuple[str, ...]) -> None:
    if expected:
        messages.assert_contains_any(*expected)


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
    from tests.e2e.baseline.toolkit.capture import reply_capture

    async with reply_capture(
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
            marker = f"HUNT-{uuid.uuid4().hex[:10]}"
            cursor = capture.messages.snapshot()
            prompt = format_value(step.prompt, marker=marker)
            message_id = await resources.user_ops.send_message(
                room_id,
                prompt,
                mention_id=running.agent.id,
                mention_name=running.agent.name,
            )
            if step.barrier == "reply":
                replies = await wait_or_exit(
                    running,
                    capture.wait_for_reply(message_id, running.agent.id, since=cursor),
                )
                expected = tuple(
                    format_value(value, marker=marker) for value in step.contains_any
                )
                assert_contains(replies, expected)
            else:
                await wait_or_exit(
                    running,
                    capture.wait_for_processed(message_id, running.agent.id),
                )
            results.append(Result(scenario, running.spec.id, "pass", f"step {index}"))


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
    from tests.e2e.baseline.toolkit.capture import reply_capture

    async with reply_capture(
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
        results.append(
            Result("collaboration", f"{source.spec.id}->{target.spec.id}", "pass")
        )


async def run_group(
    plan: Plan,
    resources: Any,
    ws: Any,
    settings: Any,
    repo: Path,
    results: list[Result],
) -> None:
    running: dict[str, RunningExample] = {}
    try:
        for spec in plan.examples:
            agent = await resources.provision_agent(f"group-{spec.id}")
            running[spec.id] = await start_example(spec, agent, repo, settings)
        async with asyncio.TaskGroup() as group:
            for item in running.values():
                group.create_task(
                    exercise_steps(item, resources, ws, settings, "together", results)
                )
        shared_room = await resources.provision_room(
            title="example-hunt-shared-room",
            participants=[item.agent.id for item in running.values()],
        )
        from tests.e2e.baseline.toolkit.capture import reply_capture

        async with reply_capture(
            ws,
            shared_room,
            user_ops=resources.user_ops,
            settings=settings,
            deadline_s=settings.e2e_timeout,
        ) as capture:
            for item in running.values():
                marker = f"HUNT-{uuid.uuid4().hex[:10]}"
                cursor = capture.messages.snapshot()
                message_id = await resources.user_ops.send_message(
                    shared_room,
                    f"Reply with the exact marker {marker}.",
                    mention_id=item.agent.id,
                    mention_name=item.agent.name,
                )
                replies = await wait_or_exit(
                    item,
                    capture.wait_for_reply(message_id, item.agent.id, since=cursor),
                )
                replies.assert_contains_any(marker)
                results.append(Result("shared-room", item.spec.id, "pass"))
        for collaboration in plan.collaborations:
            await exercise_collaboration(
                collaboration, running, resources, ws, settings, results
            )
    finally:
        await asyncio.gather(
            *(stop_example(item) for item in running.values()), return_exceptions=True
        )


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
            if "independent" in plan.topologies:
                for spec in plan.examples:
                    running: RunningExample | None = None
                    try:
                        agent = await resources.provision_agent(f"solo-{spec.id}")
                        running = await start_example(spec, agent, repo, settings)
                        await exercise_steps(
                            running, resources, ws, settings, "independent", results
                        )
                    except Exception as error:
                        results.append(
                            Result("independent", spec.id, "fail", str(error))
                        )
                    finally:
                        if running is not None:
                            await stop_example(running)
            if "together" in plan.topologies:
                try:
                    await run_group(plan, resources, ws, settings, repo, results)
                except Exception as error:
                    results.append(Result("together", "group", "fail", str(error)))
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
            f"valid plan: {len(plan.examples)} examples; topologies={','.join(plan.topologies)}"
        )
        return
    results = asyncio.run(run_live(plan, repo, args.keep))
    payload = [result.__dict__ for result in results]
    if args.json_out:
        args.json_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    for result in results:
        detail = f" — {result.detail}" if result.detail else ""
        print(f"{result.status.upper()} {result.scenario} {result.example}{detail}")
    if any(result.status == "fail" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
