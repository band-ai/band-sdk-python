#!/usr/bin/env python3
"""Run real examples through the repository's live baseline E2E boundaries.

POSIX only: every example runs in its own process group so the runner can tear
down the whole tree (``os.killpg``), which Windows has no equivalent for.

A plan file is executable input, not a sandbox: it names a program and argv the
runner spawns. Treat one exactly like a shell script you are about to run — see
the trust note in ``SKILL.md`` beside the plan schema.

stdout is this tool's interface: the incremental pass/fail lines and the final
scorecard are what a caller reads or pipes, so ``print`` is deliberate here. The
repository's no-``print`` rule governs library code under ``src/band``, not
standalone CLI entry points (see ``scripts/`` for precedent).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import tempfile
import uuid
from collections.abc import Coroutine, Iterator
from contextlib import AbstractAsyncContextManager, AsyncExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, TypeVar

import yaml

if TYPE_CHECKING:  # Real toolkit types, so drift against it is visible here.
    from band.client.streaming import MessageCreatedPayload

    # pyrefly: ignore[missing-import]
    from tests.e2e.baseline.settings import BaselineSettings

    # pyrefly: ignore[missing-import]
    from tests.e2e.baseline.toolkit.capture import ReplyCapture

    # pyrefly: ignore[missing-import]
    from tests.e2e.baseline.toolkit.observations.replies import Replies

    # pyrefly: ignore[missing-import]
    from tests.e2e.baseline.toolkit.provisioning import (
        ProvisionedAgent,
        ResourceManager,
    )

    # pyrefly: ignore[missing-import]
    from tests.e2e.baseline.toolkit.user_ops import UserOps

    # pyrefly: ignore[missing-import]
    from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient

T = TypeVar("T")


def repository_root() -> Path:
    """This script's repository root, refusing anything that is not this repo.

    The script is a standalone ``uv run`` entry point and cannot import
    ``tests.paths``, so the anchor is derived once, here. Verifying it means a
    moved or copied skill directory fails loudly instead of silently resolving
    to some parent directory and driving the wrong tree.
    """
    root = Path(__file__).resolve().parents[4]
    if not (root / "pyproject.toml").is_file() or not (root / "src" / "band").is_dir():
        raise RuntimeError(
            f"{Path(__file__).name} must live at <repo>/.claude/skills/<skill>/"
            f"scripts/; resolved a non-repository root: {root}"
        )
    return root


REPO_ROOT = repository_root()

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
HARNESS_ENDPOINT_VARIABLES = frozenset({"BAND_REST_URL", "BAND_WS_URL"})

# A child example's environment is built from this allowlist, never inherited.
# These are the variables a child needs merely to be a working process; anything
# else is set explicitly by the plan (``env``) or named by it (``forward_env``).
PROCESS_ENVIRONMENT = frozenset(
    {
        "PATH",
        "HOME",
        "TMPDIR",
        "TERM",
        "SHELL",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        # Without a trust store an example cannot verify the platform's TLS.
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        # Interpreter and tool roots, so `uv run` and the repo venv resolve the
        # same way for the child as they do for the runner.
        "VIRTUAL_ENV",
        "UV_CACHE_DIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
    }
)

# The runner authenticates as the human driver that owns every provisioned room
# and agent, and importing the baseline settings loads `.env.test` into this
# process. A child is an LLM-driven agent that runs shell commands, so it never
# receives a Band user key: holding one would let the example under test act as
# the identity testing it. Its own identity arrives via ``agent_config.yaml``.
DRIVER_CREDENTIAL_PREFIX = "BAND_API_KEY"

# A child that dies on import exits well inside this budget. A healthy example
# never exits, so this is a flat per-launch cost, paid to keep a dead-on-import
# example from being reported as a mysterious barrier timeout minutes later.
STARTUP_READINESS_S = 2.0

# Shutdown escalation budgets. SIGINT first, because examples install their own
# handlers and unwind cleanly; then SIGTERM; then SIGKILL.
TERMINATE_GRACE_S = 8.0
TERMINATE_ESCALATION_S = 4.0

# Signals whose default disposition would kill the runner outright, orphaning
# detached children that hold provisioned identities. SIGINT already unwinds
# through ``asyncio.run``, so it is deliberately absent.
TERMINATION_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


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
    forward_env: tuple[str, ...] = ()
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
class ChildLog:
    path: Path
    preserve: bool = False


@dataclass
class RunningExample:
    spec: ExampleSpec
    agent: ProvisionedAgent
    process: asyncio.subprocess.Process
    workdir: str
    log: ChildLog
    resources: AsyncExitStack


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
    """Reject a template `format` cannot fill, before anything is provisioned.

    ``IndexError`` covers positional placeholders (``{}`` and ``{0}``): only
    named values are ever supplied, so a plan using one must fail at validation
    rather than after the live agents exist.
    """
    try:
        value.format(**allowed)
    except (IndexError, KeyError, ValueError) as error:
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
    """Resolve a plan's ``path`` to a real file inside the repository.

    An existence-and-location check, not a sandbox: it catches a typo or a stale
    path before anything is provisioned, and supplies the ``{path}`` placeholder.
    The plan's ``command`` is arbitrary argv and need not reference this file, so
    running a plan is exactly as privileged as running its author's script.
    """
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


def validate_environment_ownership(spec: ExampleSpec) -> None:
    """Reject a plan claiming variables the harness owns or must never hand over."""
    configured = {name for name, _ in spec.environment} | set(spec.forward_env)
    reserved = sorted(configured & HARNESS_ENDPOINT_VARIABLES)
    if reserved:
        raise ValueError(
            "harness endpoint variables cannot be configured by a plan: "
            + ", ".join(reserved)
        )
    driver = sorted(
        name for name in configured if name.startswith(DRIVER_CREDENTIAL_PREFIX)
    )
    if driver:
        raise ValueError(
            "a child example must never receive the run's Band credentials: "
            + ", ".join(driver)
        )


def parse_example(raw: Any, index: int, repo: Path) -> ExampleSpec:
    label = f"examples[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a mapping")
    if "unset_env" in raw:
        raise ValueError(
            f"{label}.unset_env is obsolete: the child environment is an "
            "allowlist, so nothing ambient leaks in; name what it needs in "
            "forward_env instead"
        )
    example_id = required_string(raw, "id", label)
    config_key = required_string(raw, "config_key", label)
    relative_path = required_string(raw, "path", label)
    spec = ExampleSpec(
        id=example_id,
        path=resolve_example_path(repo, relative_path),
        config_key=config_key,
        command=strings(raw.get("command"), "command"),
        environment=parse_environment(raw.get("env", {}), f"{label}.env"),
        forward_env=strings(raw.get("forward_env"), "forward_env"),
        steps=parse_steps(raw.get("steps", []), f"{label}.steps"),
    )
    validate_process_templates(spec)
    validate_environment_ownership(spec)
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


def reply_capture_context(
    ws: TrackingWebSocketClient,
    room_id: str,
    *,
    user_ops: UserOps,
    settings: BaselineSettings,
    deadline_s: float,
) -> AbstractAsyncContextManager[ReplyCapture]:
    """Open a baseline reply capture; imported late, after ``sys.path`` is set."""
    # pyrefly: ignore[missing-import]
    from tests.e2e.baseline.toolkit.capture import reply_capture

    return reply_capture(
        ws, room_id, user_ops=user_ops, settings=settings, deadline_s=deadline_s
    )


def process_values(spec: ExampleSpec, repo: Path, workdir: str) -> dict[str, str]:
    return {"repo": str(repo), "path": str(spec.path), "workdir": workdir}


def write_agent_config(
    spec: ExampleSpec, agent: ProvisionedAgent, workdir: str
) -> None:
    """Write the child's generated config, owner-only from the moment it exists.

    It carries the provisioned agent's API key, so the mode is passed to
    ``open`` rather than chmod-ed afterwards: a create-then-chmod leaves the key
    world-readable for the window in between.
    """
    document = yaml.safe_dump(
        {spec.config_key: {"agent_id": agent.id, "api_key": agent.api_key}}
    )
    path = Path(workdir) / "agent_config.yaml"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(document)


def example_command(spec: ExampleSpec, repo: Path, workdir: str) -> tuple[str, ...]:
    values = process_values(spec, repo, workdir)
    return tuple(part.format(**values) for part in spec.command) or (
        sys.executable,
        str(spec.path),
    )


def example_environment(
    spec: ExampleSpec, repo: Path, workdir: str, settings: BaselineSettings
) -> dict[str, str]:
    """Build the child's whole environment from an allowlist, never inheriting.

    An example is an LLM-driven agent that runs shell commands, so it gets the
    process basics, exactly what its plan declares, and the harness endpoints —
    not the runner's own credential-laden environment. Reading ``os.environ``
    here is deliberate process construction, not configuration lookup.
    """
    values = process_values(spec, repo, workdir)
    environment = {
        name: os.environ[name]
        for name in PROCESS_ENVIRONMENT | set(spec.forward_env)
        if name in os.environ
    }
    environment.update(
        {name: value.format(**values) for name, value in spec.environment}
    )
    environment.update(
        BAND_REST_URL=settings.endpoints.rest_url,
        BAND_WS_URL=settings.endpoints.ws_url,
        PYTHONPATH=str(repo),
    )
    # Defence in depth: plan validation already rejects these, but the one place
    # a child environment is built is where the guarantee is worth restating.
    return {
        name: value
        for name, value in environment.items()
        if not name.startswith(DRIVER_CREDENTIAL_PREFIX)
    }


@contextmanager
def child_log(spec: ExampleSpec) -> Iterator[tuple[ChildLog, IO[bytes]]]:
    log_file = tempfile.NamedTemporaryFile(
        mode="ab", prefix=f"band-example-{spec.id}-", suffix=".log", delete=False
    )
    log_path = Path(log_file.name)
    log_path.chmod(0o600)
    artifact = ChildLog(log_path)
    try:
        yield artifact, log_file
    finally:
        log_file.close()
        if not artifact.preserve:
            log_path.unlink(missing_ok=True)


async def start_example(
    spec: ExampleSpec,
    agent: ProvisionedAgent,
    repo: Path,
    settings: BaselineSettings,
) -> RunningExample:
    resources = AsyncExitStack()
    # One cleanup path for every way startup can fail, the readiness wait
    # included: a cancellation there would otherwise leak a spawned child that
    # nothing else owns yet.
    try:
        workdir = resources.enter_context(
            tempfile.TemporaryDirectory(prefix=f"band-example-{spec.id}-")
        )
        log, log_file = resources.enter_context(child_log(spec))
        write_agent_config(spec, agent, workdir)
        process = await asyncio.create_subprocess_exec(
            *example_command(spec, repo, workdir),
            cwd=workdir,
            env=example_environment(spec, repo, workdir, settings),
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        resources.push_async_callback(terminate_process, process)
        running = RunningExample(
            spec=spec,
            agent=agent,
            process=process,
            workdir=workdir,
            log=log,
            resources=resources,
        )
        await confirm_started(running)
        return running
    except BaseException:
        await resources.aclose()
        raise


async def confirm_started(running: RunningExample) -> None:
    """Fail if the example dies during startup — a bad import, config, or command.

    Waiting is the only honest check: a child that has just exited still reports
    ``returncode is None`` until something awaits it, so polling right after the
    spawn always says "running" and lets a dead example through.
    """
    try:
        status = await asyncio.wait_for(running.process.wait(), STARTUP_READINESS_S)
    except TimeoutError:
        return
    running.log.preserve = True
    raise RuntimeError(
        f"{running.spec.id} exited with status {status} during startup; "
        f"child log: {running.log.path}"
    )


async def wait_or_exit(running: RunningExample, awaitable: Coroutine[Any, Any, T]) -> T:
    """Await a scenario barrier, failing fast if the example exits first.

    Both tasks are cancelled on the way out, including when this coroutine is
    itself cancelled (a failing sibling step cancels the whole task group):
    ``asyncio.wait`` leaves what it waited on running, and a leaked reply wait
    keeps polling a capture whose channel has already been left.
    """
    boundary = asyncio.create_task(awaitable)
    exited = asyncio.create_task(running.process.wait())
    try:
        done, _ = await asyncio.wait(
            {boundary, exited}, return_when=asyncio.FIRST_COMPLETED
        )
        if exited in done:
            running.log.preserve = True
            raise RuntimeError(
                f"{running.spec.id} exited with status {exited.result()}; "
                f"child log: {running.log.path}"
            )
        return boundary.result()
    finally:
        boundary.cancel()
        exited.cancel()
        await asyncio.gather(boundary, exited, return_exceptions=True)


def signal_process(process: asyncio.subprocess.Process, signal_number: int) -> None:
    """Signal the example's whole process group, so its own children die with it."""
    try:
        os.killpg(process.pid, signal_number)
    except ProcessLookupError:
        pass


async def terminate_process(process: asyncio.subprocess.Process) -> None:
    """Escalate through the process group until the example is gone."""
    if process.returncode is not None:
        return
    escalation = (
        (signal.SIGINT, TERMINATE_GRACE_S),
        (signal.SIGTERM, TERMINATE_ESCALATION_S),
        (signal.SIGKILL, None),
    )
    for number, budget in escalation:
        signal_process(process, number)
        if budget is None:
            await process.wait()
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=budget)
            return
        except TimeoutError:
            continue


@contextmanager
def cancel_on_termination() -> Iterator[None]:
    """Turn SIGTERM/SIGHUP into cancellation of the running task.

    Their default disposition kills the runner outright, so the cleanup that
    stops examples and reaps provisioned identities never runs and detached
    children survive as orphans burning LLM budget. Cancelling instead unwinds
    the same path Ctrl-C already takes.
    """
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("cancel_on_termination requires a running task")
    for number in TERMINATION_SIGNALS:
        loop.add_signal_handler(number, task.cancel)
    try:
        yield
    finally:
        for number in TERMINATION_SIGNALS:
            loop.remove_signal_handler(number)


async def stop_example(running: RunningExample) -> None:
    await running.resources.aclose()


def assert_contains(messages: Replies, expected: tuple[str, ...]) -> None:
    """Assert some reply carries an expected value, when the step declared any.

    ``assert_contains_any`` takes one iterable of options; splatting the tuple
    passes the first string *as* the iterable, so its characters become the
    options and any reply containing any single letter of a marker passes.
    """
    if expected:
        messages.assert_contains_any(expected)


def parse_server_timestamp(value: datetime | str | None) -> datetime:
    """Coerce a platform timestamp to an aware UTC stamp (the platform stores UTC)."""
    if value is None:
        raise TypeError("platform message timestamp is missing")
    stamp = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


async def message_server_timestamp(
    user_ops: UserOps, room_id: str, message_id: str
) -> datetime:
    messages = await user_ops.list_messages(room_id, limit=100)
    message = next((item for item in messages if item.id == message_id), None)
    if message is None:
        raise RuntimeError(f"trigger message {message_id} is missing from room history")
    return parse_server_timestamp(message.inserted_at)


async def wait_for_step(
    step: Step,
    running: RunningExample,
    capture: ReplyCapture,
    message_id: str,
    cursor: int,
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
    step: Step, running: RunningExample, capture: ReplyCapture, since: datetime
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
    resources: ResourceManager,
    capture: ReplyCapture,
    room_id: str,
) -> None:
    marker = f"HUNT-{uuid.uuid4().hex[:10]}"
    cursor = capture.messages.snapshot()
    prompt = format_value(step.prompt, marker=marker, room_id=room_id)
    message_id = await resources.user_ops.send_message(
        room_id,
        prompt,
        mention_id=running.agent.id,
        mention_name=running.agent.name,
    )
    since = await message_server_timestamp(resources.user_ops, room_id, message_id)
    await wait_for_step(step, running, capture, message_id, cursor, marker, room_id)
    await assert_step_tools(step, running, capture, since)


async def exercise_steps(
    running: RunningExample,
    resources: ResourceManager,
    ws: TrackingWebSocketClient,
    settings: BaselineSettings,
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
    resources: ResourceManager,
    ws: TrackingWebSocketClient,
    settings: BaselineSettings,
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
    resources: ResourceManager,
    ws: TrackingWebSocketClient,
    settings: BaselineSettings,
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

        # Reads the capture's own ``Replies`` rather than the predicate argument,
        # which the toolkit declares as a plain list and so has no window helpers.
        def target_replied(_messages: list[MessageCreatedPayload]) -> bool:
            replies = capture.messages.since(cursor).from_sender(target.agent.id)
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
    resources: ResourceManager,
    settings: BaselineSettings,
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
    resources: ResourceManager,
    ws: TrackingWebSocketClient,
    settings: BaselineSettings,
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
    resources: ResourceManager,
    capture: ReplyCapture,
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
    assert_contains(replies, (marker,))


async def exercise_shared_turn_reported(
    running: RunningExample,
    resources: ResourceManager,
    capture: ReplyCapture,
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
    resources: ResourceManager,
    ws: TrackingWebSocketClient,
    settings: BaselineSettings,
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
    resources: ResourceManager,
    ws: TrackingWebSocketClient,
    settings: BaselineSettings,
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


async def stop_examples(
    running: dict[str, RunningExample], results: list[Result]
) -> None:
    examples = list(running.values())
    outcomes = await asyncio.gather(
        *(stop_example(item) for item in running.values()), return_exceptions=True
    )
    for item, outcome in zip(examples, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            record_result(
                results,
                Result("cleanup", item.spec.id, "fail", str(outcome)),
            )


async def run_group(
    plan: Plan,
    resources: ResourceManager,
    ws: TrackingWebSocketClient,
    settings: BaselineSettings,
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
        await stop_examples(running, results)


async def run_independent_example(
    spec: ExampleSpec,
    resources: ResourceManager,
    ws: TrackingWebSocketClient,
    settings: BaselineSettings,
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
            try:
                await stop_example(running)
            except Exception as error:
                record_result(results, Result("cleanup", spec.id, "fail", str(error)))


async def run_independent_examples(
    plan: Plan,
    resources: ResourceManager,
    ws: TrackingWebSocketClient,
    settings: BaselineSettings,
    repo: Path,
    results: list[Result],
) -> None:
    for spec in plan.examples:
        await run_independent_example(spec, resources, ws, settings, repo, results)


async def run_live(plan: Plan, repo: Path, keep: bool, results: list[Result]) -> None:
    """Drive the whole plan, accumulating into the caller's ``results``.

    The caller owns the list so the scorecard survives a crashed run: a failure
    in the observer or in cleanup must not discard the outcomes already earned.
    """
    sys.path.insert(0, str(repo))
    # pyrefly: ignore[missing-import]
    from tests.e2e.baseline.settings import BaselineSettings

    # pyrefly: ignore[missing-import]
    from tests.e2e.baseline.toolkit.provisioning import (
        ResourceManager,
        user_rest_client,
    )

    # pyrefly: ignore[missing-import]
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
    with cancel_on_termination():
        try:
            async with user_ws_observer(settings) as ws:
                await run_independent_examples(
                    plan, resources, ws, settings, repo, results
                )
                try:
                    await run_group(plan, resources, ws, settings, repo, results)
                except Exception as error:
                    record_result(
                        results, Result("together", "group", "fail", str(error))
                    )
        finally:
            if not keep:
                await resources.reap_all()


def report_scorecard(results: list[Result], json_out: Path | None) -> None:
    """Emit the run's final scorecard, whatever ended the run."""
    if json_out:
        payload = [result.__dict__ for result in results]
        json_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    passed = sum(result.status == "pass" for result in results)
    failed = sum(result.status == "fail" for result in results)
    print(f"SUMMARY passed={passed} failed={failed}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("plan", type=Path)
    result.add_argument("--repo", type=Path, default=REPO_ROOT)
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
    results: list[Result] = []
    try:
        asyncio.run(run_live(plan, repo, args.keep, results))
    finally:
        report_scorecard(results, args.json_out)
    if any(result.status == "fail" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
