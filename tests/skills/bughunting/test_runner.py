"""Tests for the bug-hunting skill's live example runner.

The doubles here fake only the WebSocket feed: replies and tool calls are the
baseline toolkit's own ``Replies``/``ToolCalls``, so a call the runner makes
wrongly against the toolkit it hard-depends on fails here as it would live.

Signals, process groups and startup death are exercised against real child
processes (see ``probes.py``) — a mock can report any ``returncode`` it likes,
which is exactly how a dead startup guard passed its test before.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import yaml
from band_rest.types.chat_message import ChatMessage

from band.client.streaming import MessageCreatedPayload

from tests.e2e.baseline.toolkit.observations.replies import Replies
from tests.e2e.baseline.toolkit.observations.tool_calls import ToolCall, ToolCalls
from tests.paths import REPO_ROOT

# The runner drives child process groups (os.killpg, SIGHUP, SIGKILL) and writes
# owner-only credential files; none of that has a Windows equivalent.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the example runner is POSIX-only"
)

PROBES = Path(__file__).with_name("probes.py")
AGENT_ID = "agent-id"
# Deadline for observing a real process die; generous, and only ever waited out
# when the assertion is about to fail anyway.
PROCESS_DEADLINE_S = 5.0


# --- doubles and builders -----------------------------------------------------


def settings() -> SimpleNamespace:
    return SimpleNamespace(
        endpoints=SimpleNamespace(
            rest_url="https://test.invalid", ws_url="wss://test.invalid/socket"
        ),
        e2e_timeout=1,
    )


def agent(name: str = "agent", agent_id: str = AGENT_ID) -> SimpleNamespace:
    return SimpleNamespace(id=agent_id, api_key="private-key", name=name)


def reply(content: str, *, sender_id: str = AGENT_ID) -> MessageCreatedPayload:
    now = "2001-01-01T00:00:00Z"
    return MessageCreatedPayload(
        id="reply",
        content=content,
        message_type="text",
        sender_id=sender_id,
        sender_type="Agent",
        inserted_at=now,
        updated_at=now,
    )


class Capture:
    """Stands in for ``ReplyCapture``, handing back real observation objects."""

    def __init__(
        self, *, replies: Replies | None = None, calls: ToolCalls | None = None
    ) -> None:
        self.messages = Replies() if replies is None else replies
        self.calls = ToolCalls() if calls is None else calls
        self.tool_reads: list[dict[str, Any]] = []

    async def wait_for_processed(self, message_id: str, recipient_id: str) -> None:
        return None

    async def wait_for_reply(
        self, message_id: str, recipient_id: str, *, since: int = 0
    ) -> Replies:
        return self.messages.since(since).from_sender(recipient_id)

    async def wait_until(
        self, predicate: Callable[[Replies], bool], *, deadline_s: float | None = None
    ) -> Replies:
        if not predicate(self.messages):
            raise TimeoutError("predicate never held")
        return self.messages

    async def tool_calls(self, **kwargs: Any) -> ToolCalls:
        self.tool_reads.append(kwargs)
        return self.calls


Responder = Callable[[str], list[MessageCreatedPayload]]


class UserOps:
    """Records what the driver sent, and lands the replies a turn produces.

    ``responder`` sees the prompt, so a reply can carry that turn's correlated
    marker — which is the whole point of the runner's expectations.
    """

    def __init__(
        self, capture: Capture | None = None, responder: Responder | None = None
    ) -> None:
        self.sent: list[ChatMessage] = []
        self.capture = capture
        self.responder = responder

    async def send_message(
        self, room_id: str, content: str, *, mention_id: str, mention_name: str
    ) -> str:
        message_id = f"message-{len(self.sent) + 1}"
        self.sent.append(
            ChatMessage(
                id=message_id,
                content=content,
                message_type="text",
                sender_id="user-id",
                sender_type="User",
                # Distinct per message, so a step's tool window is provably its own.
                inserted_at=datetime(
                    2001, 1, 1, 0, 0, 0, 100000 * len(self.sent) + 100000, timezone.utc
                ),
            )
        )
        if self.capture is not None and self.responder is not None:
            self.capture.messages.extend(self.responder(content))
        return message_id

    async def list_messages(self, room_id: str, **kwargs: Any) -> list[ChatMessage]:
        return list(self.sent)


class Resources:
    def __init__(
        self, capture: Capture | None = None, responder: Responder | None = None
    ) -> None:
        self.user_ops = UserOps(capture, responder)

    async def provision_room(self, **kwargs: Any) -> str:
        return "room-id"


def marker_of(prompt: str) -> str:
    """The correlated marker the runner put in a prompt."""
    return next(word for word in prompt.split() if word.startswith("HUNT-"))


class WaitingProcess:
    returncode = None

    async def wait(self) -> int:
        await asyncio.Event().wait()
        return 0


def running_example(
    runner: ModuleType, spec: Any, *, name: str = "agent", agent_id: str = AGENT_ID
) -> SimpleNamespace:
    return SimpleNamespace(
        spec=spec,
        agent=agent(name, agent_id),
        process=WaitingProcess(),
        log=SimpleNamespace(path=Path("/tmp/example.log"), preserve=False),
    )


def install_capture(monkeypatch: pytest.MonkeyPatch, capture: Capture) -> None:
    """Make the runner's late-imported ``reply_capture`` yield ``capture``."""
    import tests.e2e.baseline.toolkit.capture as capture_module  # noqa: PLC0415

    @asynccontextmanager
    async def factory(*args: Any, **kwargs: Any) -> AsyncIterator[Capture]:
        yield capture

    monkeypatch.setattr(capture_module, "reply_capture", factory)


def example(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "one",
        "path": "examples/example.py",
        "config_key": "agent",
        **overrides,
    }


def plan(*examples: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    return {"version": 1, "examples": list(examples) or [example()], **overrides}


@contextmanager
def permissive_umask() -> Iterator[None]:
    """Clear the umask, so a mode assertion proves the mode was requested."""
    previous = os.umask(0)
    try:
        yield
    finally:
        os.umask(previous)


async def read_pids(path: Path, count: int) -> list[int]:
    """Wait for a probe to record ``count`` pids, then return them."""
    async with asyncio.timeout(PROCESS_DEADLINE_S):
        while True:
            pids = path.read_text(encoding="utf-8").split() if path.is_file() else []
            if len(pids) == count:
                return [int(pid) for pid in pids]
            await asyncio.sleep(0.05)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


async def wait_until_gone(pid: int) -> None:
    async with asyncio.timeout(PROCESS_DEADLINE_S):
        while alive(pid):
            await asyncio.sleep(0.05)


# --- plan validation ----------------------------------------------------------


def test_valid_plan_parses_every_declared_field(
    runner: ModuleType, plan_repo: Path, write_plan: Callable[[dict[str, Any]], Path]
) -> None:
    document = plan(
        example(
            command=["{repo}/.venv/bin/python", "{path}"],
            env={"AGENT_CWD": "{workdir}"},
            forward_env=["ANTHROPIC_API_KEY"],
            steps=[
                {
                    "prompt": "Inspect {room_id} and reply {marker}.",
                    "contains_any": ["{marker}"],
                    "tools": ["band_get_participants"],
                    "tool_calls_at_least": 1,
                }
            ],
        ),
        example(id="two", config_key="second"),
        collaborations=[{"source": "one", "target": "two", "prompt": "ping {marker}"}],
    )

    parsed = runner.load_plan(write_plan(document), plan_repo)

    spec = parsed.examples[0]
    assert [item.id for item in parsed.examples] == ["one", "two"]
    assert spec.path == plan_repo / "examples" / "example.py"
    assert spec.forward_env == ("ANTHROPIC_API_KEY",)
    assert spec.environment == (("AGENT_CWD", "{workdir}"),)
    assert spec.steps[0].tools == ("band_get_participants",)
    assert [(item.source, item.target) for item in parsed.collaborations] == [
        ("one", "two")
    ]


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"examples": [example()]}, "plan.version must be 1"),
        (plan(topologies=["independent"]), "topologies is not configurable"),
        ({"version": 1, "examples": []}, "plan.examples must be a non-empty list"),
        (plan(example(), example()), "example ids must be unique"),
        (plan(example(path="../outside.py")), "does not exist inside the repository"),
        (plan(example(path="/etc/passwd")), "does not exist inside the repository"),
        (plan(example(path="examples/missing.py")), "does not exist inside the repo"),
        (plan(example(config_key="")), r"config_key must be a non-empty string"),
        (plan(example(command=["{bogus}"])), "invalid command template"),
        (plan(example(command="not-a-list")), "command must be a list of strings"),
        (plan(example(env={"A": 1})), "env must map strings to strings"),
        (
            plan(example(env={"BAND_REST_URL": "https://elsewhere.invalid"})),
            "harness endpoint variables cannot be configured",
        ),
        (
            plan(example(forward_env=["BAND_WS_URL"])),
            "harness endpoint variables cannot be configured",
        ),
        (
            plan(example(forward_env=["BAND_API_KEY_USER"])),
            "must never receive the run's Band credentials",
        ),
        (plan(example(unset_env=["GITHUB_TOKEN"])), "unset_env is obsolete"),
        (
            plan(example(steps=[{"prompt": "reply {0}"}])),
            "invalid step prompt template",
        ),
        (plan(example(steps=[{"prompt": "reply {}"}])), "invalid step prompt template"),
        (
            plan(example(steps=[{"prompt": "reply {bogus}"}])),
            "invalid step prompt template",
        ),
        (
            plan(example(steps=[{"prompt": "hi", "barrier": "eventually"}])),
            "unsupported barrier",
        ),
        (
            plan(example(steps=[{"prompt": "hi", "tool_calls_at_least": -1}])),
            "tool_calls_at_least must be a non-negative integer",
        ),
        (
            plan(example(steps=[{"prompt": "hi", "tool_calls_at_least": True}])),
            "tool_calls_at_least must be a non-negative integer",
        ),
        (
            plan(collaborations=[{"source": "one", "target": "ghost", "prompt": "hi"}]),
            "requires known source and target",
        ),
        (
            plan(collaborations=[{"source": "one", "target": "one", "prompt": "{0}"}]),
            "invalid collaboration prompt template",
        ),
    ],
)
def test_load_plan_rejects_before_anything_is_provisioned(
    runner: ModuleType,
    plan_repo: Path,
    write_plan: Callable[[dict[str, Any]], Path],
    document: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        runner.load_plan(write_plan(document), plan_repo)


def test_example_path_may_not_escape_the_repository_by_symlink(
    runner: ModuleType, plan_repo: Path, tmp_path: Path
) -> None:
    outside = tmp_path.parent / "outside.py"
    outside.write_text("", encoding="utf-8")
    (plan_repo / "examples" / "escape.py").symlink_to(outside)

    with pytest.raises(ValueError, match="does not exist inside the repository"):
        runner.resolve_example_path(plan_repo, "examples/escape.py")


# --- child process environment and configuration ------------------------------


def test_child_environment_never_carries_the_driver_identity(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child is built from an allowlist, so the runner's own keys stay put.

    The runner authenticates as the human driver that owns the run's rooms and
    agents, and importing the baseline settings loads `.env.test` into this
    process; a child that inherited it could act as the identity testing it.
    """
    monkeypatch.setenv("BAND_API_KEY_USER", "driver-key")
    monkeypatch.setenv("BAND_API_KEY", "ambient-agent-key")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "llm-key")
    monkeypatch.setenv("PATH", "/usr/bin")
    spec = runner.ExampleSpec(
        "example", Path("example.py"), "agent", forward_env=("ANTHROPIC_API_KEY",)
    )

    environment = runner.example_environment(
        spec, Path("/repo"), "/tmp/run", settings()
    )

    assert "BAND_API_KEY_USER" not in environment
    assert "BAND_API_KEY" not in environment
    assert "GITHUB_TOKEN" not in environment  # ambient, and not declared
    assert environment["ANTHROPIC_API_KEY"] == "llm-key"  # declared, so forwarded
    assert environment["PATH"] == "/usr/bin"
    assert environment["PYTHONPATH"] == "/repo"


def test_harness_endpoints_cannot_be_overridden(runner: ModuleType) -> None:
    spec = runner.ExampleSpec(
        "example",
        Path("example.py"),
        "agent",
        environment=(("BAND_REST_URL", "https://production.invalid"),),
        forward_env=("BAND_WS_URL",),
    )
    with pytest.raises(ValueError, match="BAND_REST_URL, BAND_WS_URL"):
        runner.validate_environment_ownership(spec)

    environment = runner.example_environment(
        spec, Path("/repo"), "/tmp/run", settings()
    )
    assert environment["BAND_REST_URL"] == "https://test.invalid"
    assert environment["BAND_WS_URL"] == "wss://test.invalid/socket"


def test_agent_config_is_owner_only(runner: ModuleType, tmp_path: Path) -> None:
    """The generated config holds the agent's API key, so its mode is requested
    at creation rather than inherited from the umask and narrowed afterwards."""
    spec = runner.ExampleSpec("example", Path("example.py"), "darter")

    with permissive_umask():
        runner.write_agent_config(spec, agent(), str(tmp_path))

    path = tmp_path / "agent_config.yaml"
    assert path.stat().st_mode & 0o777 == 0o600
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
        "darter": {"agent_id": AGENT_ID, "api_key": "private-key"}
    }


def test_child_log_is_private(runner: ModuleType) -> None:
    spec = runner.ExampleSpec("example", Path("example.py"), "agent")
    with runner.child_log(spec) as (artifact, log_file):
        log_file.write(b"private diagnostic\n")
        log_file.flush()
        assert artifact.path.stat().st_mode & 0o777 == 0o600
        assert artifact.path.read_bytes() == b"private diagnostic\n"
    assert not artifact.path.exists()


# --- startup, shutdown and signals (real child processes) ---------------------


@pytest.mark.asyncio
async def test_startup_death_is_caught_and_diagnosed(runner: ModuleType) -> None:
    """A real child that dies on import must be reported at startup.

    Polling cannot see this: a process that has just exited still reports
    ``returncode is None`` until something awaits it.
    """
    spec = runner.ExampleSpec(
        "example",
        Path("example.py"),
        "agent",
        command=(
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('bad import\\n'); sys.exit(3)",
        ),
    )

    with pytest.raises(
        RuntimeError, match="exited with status 3 during startup"
    ) as bad:
        await runner.start_example(spec, agent(), REPO_ROOT, settings())

    log_path = Path(str(bad.value).partition("child log: ")[2])
    try:
        assert log_path.stat().st_mode & 0o777 == 0o600
        assert log_path.read_text(encoding="utf-8") == "bad import\n"
    finally:
        log_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_healthy_child_survives_the_readiness_budget(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The branch is under test, not the production budget's value.
    monkeypatch.setattr(runner, "STARTUP_READINESS_S", 0.3)
    spec = runner.ExampleSpec(
        "example",
        Path("example.py"),
        "agent",
        command=(sys.executable, "-c", "import time; time.sleep(300)"),
    )

    running = await runner.start_example(spec, agent(), REPO_ROOT, settings())
    try:
        assert running.process.returncode is None
    finally:
        await runner.stop_example(running)

    await wait_until_gone(running.process.pid)
    assert not running.log.path.exists()  # a clean stop keeps no diagnostics


@pytest.mark.asyncio
async def test_termination_escalates_across_the_whole_process_group(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A child that ignores SIGINT is escalated to, and its own child dies too.

    The example's subprocesses (a CLI backend, a server) are only reached because
    signals go to the process group, so this is what keeps them from orphaning.
    """
    # The escalation sequence is under test, not the production budgets.
    monkeypatch.setattr(runner, "TERMINATE_GRACE_S", 0.3)
    pid_file = tmp_path / "pids"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(PROBES),
        "sleep",
        "--pid-file",
        str(pid_file),
        "--ignore-sigint",
        "--with-peer",
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        start_new_session=True,  # its own process group, as start_example does
    )
    child, peer = await read_pids(pid_file, 2)

    await runner.terminate_process(process)

    assert process.returncode == -signal.SIGTERM  # SIGINT alone could not end it
    assert child == process.pid
    await wait_until_gone(peer)


@pytest.mark.asyncio
async def test_sigterm_runs_cleanup_instead_of_orphaning_children(
    tmp_path: Path,
) -> None:
    """SIGTERM must unwind the run, not kill the runner over its own children.

    Children are started in their own session, so nothing else would ever stop
    them: they would keep holding provisioned identities and burning LLM budget.
    """
    pid_file = tmp_path / "pids"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(PROBES),
        "termination",
        "--pid-file",
        str(pid_file),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    (child,) = await read_pids(pid_file, 1)
    assert process.stdout is not None
    async with asyncio.timeout(PROCESS_DEADLINE_S):
        # Signal only once the run is steady, past startup.
        assert await process.stdout.readline() == b"READY\n"

    process.send_signal(signal.SIGTERM)
    async with asyncio.timeout(PROCESS_DEADLINE_S):
        stdout, _ = await process.communicate()

    assert b"CLEANED" in stdout, stdout.decode()
    assert process.returncode == 0  # cancelled and unwound, not killed
    await wait_until_gone(child)


@pytest.mark.asyncio
async def test_process_exit_preserves_private_child_log(runner: ModuleType) -> None:
    class ExitedProcess:
        async def wait(self) -> int:
            return 7

    running = SimpleNamespace(
        spec=SimpleNamespace(id="example"),
        process=ExitedProcess(),
        log=SimpleNamespace(path=Path("/tmp/example.log"), preserve=False),
    )

    with pytest.raises(
        RuntimeError,
        match=r"^example exited with status 7; child log: /tmp/example.log$",
    ):
        await runner.wait_or_exit(running, asyncio.Event().wait())
    assert running.log.preserve is True


@pytest.mark.asyncio
async def test_cancelling_a_step_cancels_both_of_its_waits(
    runner: ModuleType,
) -> None:
    """A failing sibling cancels the whole group; nothing may keep waiting.

    ``asyncio.wait`` leaves what it waited on running, so a leaked reply wait
    goes on polling a capture whose channel has already been left.
    """
    started = asyncio.Event()
    boundary_cancelled = asyncio.Event()
    exit_cancelled = asyncio.Event()

    async def barrier() -> None:
        try:
            started.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            boundary_cancelled.set()
            raise

    class Process:
        returncode = None

        async def wait(self) -> int:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                exit_cancelled.set()
                raise
            return 0

    running = SimpleNamespace(
        spec=SimpleNamespace(id="example"),
        process=Process(),
        log=SimpleNamespace(path=Path("/tmp/example.log"), preserve=False),
    )
    step = asyncio.create_task(runner.wait_or_exit(running, barrier()))
    await started.wait()

    step.cancel()
    with pytest.raises(asyncio.CancelledError):
        await step

    assert boundary_cancelled.is_set()
    assert exit_cancelled.is_set()


# --- step and collaboration assertions ---------------------------------------


@pytest.mark.asyncio
async def test_each_step_uses_its_own_tool_boundary(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = Capture(calls=ToolCalls([ToolCall(name="band_store_memory")]))
    install_capture(monkeypatch, capture)
    spec = runner.ExampleSpec(
        id="example",
        path=Path("example.py"),
        config_key="agent",
        steps=(
            runner.Step("silent", barrier="processed"),
            runner.Step("memory", tools=("band_store_memory",), tool_calls_at_least=1),
        ),
    )
    results: list[Any] = []

    await runner.exercise_steps(
        running_example(runner, spec),
        Resources(capture, lambda prompt: [reply("done")]),
        object(),
        settings(),
        "independent",
        results,
    )

    assert [(item.status, item.detail) for item in results] == [
        ("pass", "step 1"),
        ("pass", "step 2"),
    ]
    # Only the tool-asserting step reads tool events, scoped to its own trigger.
    assert [read["since"] for read in capture.tool_reads] == [
        datetime(2001, 1, 1, 0, 0, 0, 200000, tzinfo=timezone.utc)
    ]
    assert capture.tool_reads[0]["include_memory"] is True


@pytest.mark.asyncio
async def test_a_step_fails_when_a_promised_tool_never_fired(
    runner: ModuleType,
) -> None:
    step = runner.Step("prompt", tools=("band_send_message",))
    capture = Capture(calls=ToolCalls([ToolCall(name="band_store_memory")]))

    with pytest.raises(AssertionError, match="expected tool 'band_send_message'"):
        await runner.assert_step_tools(
            step, running_example(runner, None), capture, datetime.now(timezone.utc)
        )


@pytest.mark.asyncio
async def test_a_step_fails_when_too_few_tools_fired(runner: ModuleType) -> None:
    step = runner.Step("prompt", tool_calls_at_least=3)
    capture = Capture(calls=ToolCalls([ToolCall(name="band_send_message")]))

    with pytest.raises(AssertionError, match=r"at least 3 tool call\(s\), observed 1"):
        await runner.assert_step_tools(
            step, running_example(runner, None), capture, datetime.now(timezone.utc)
        )


@pytest.mark.asyncio
async def test_a_step_fails_when_no_reply_carries_the_marker(
    runner: ModuleType,
) -> None:
    """The marker must appear whole in a reply.

    ``assert_contains_any`` takes one iterable of options, so splatting the
    expectations passed the marker's *characters* as the options and any prose
    sharing a single letter with it satisfied the step.
    """
    step = runner.Step("prompt", contains_any=("{marker}",))
    running = running_example(runner, SimpleNamespace(id="example"))
    capture = Capture(replies=Replies([reply("totally unrelated prose")]))

    with pytest.raises(AssertionError, match="no text message contained any of"):
        await runner.wait_for_step(
            step, running, capture, "message-1", 0, "HUNT-abc123", "room-id"
        )

    capture.messages.append(reply("here it is: HUNT-abc123"))
    await runner.wait_for_step(
        step, running, capture, "message-1", 0, "HUNT-abc123", "room-id"
    )


@pytest.mark.asyncio
async def test_collaboration_passes_only_when_the_target_replies(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = Capture()
    install_capture(monkeypatch, capture)
    running = {
        "first": running_example(
            runner, SimpleNamespace(id="first"), name="first", agent_id="first-id"
        ),
        "second": running_example(
            runner, SimpleNamespace(id="second"), name="second", agent_id="second-id"
        ),
    }
    collaboration = runner.Collaboration(
        source="first",
        target="second",
        prompt="Send {marker} to @{target_name}.",
        contains_any=("{marker}",),
    )
    results: list[Any] = []

    # Only the source spoke, so the directed probe never completed.
    source_only = Resources(
        capture, lambda prompt: [reply("relaying now", sender_id="first-id")]
    )
    await runner.exercise_collaborations(
        (collaboration,), running, source_only, object(), settings(), results
    )

    # Now the target answers, carrying that turn's correlated marker.
    target_replies = Resources(
        capture,
        lambda prompt: [reply(f"got {marker_of(prompt)}", sender_id="second-id")],
    )
    await runner.exercise_collaborations(
        (collaboration,), running, target_replies, object(), settings(), results
    )

    assert [(item.example, item.status) for item in results] == [
        ("first->second", "fail"),
        ("first->second", "pass"),
    ]
    # The prompt named the target, so the probe is genuinely directed.
    assert "@second" in target_replies.user_ops.sent[0].content


# --- reporting ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_reported_step_failure_keeps_example_and_capability(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("capability failed")

    monkeypatch.setattr(runner, "exercise_steps", fail)
    running = SimpleNamespace(spec=SimpleNamespace(id="second"))
    results: list[Any] = []

    await runner.exercise_steps_reported(
        running, object(), object(), object(), "together", results
    )

    assert [result.__dict__ for result in results] == [
        {
            "scenario": "together",
            "example": "second",
            "status": "fail",
            "detail": "steps: capability failed",
        }
    ]


@pytest.mark.asyncio
async def test_group_startup_failure_is_reported_per_example(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_start(spec: Any, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError(f"{spec.id} failed")

    monkeypatch.setattr(runner, "start_example", fail_start)

    class Provisioner:
        async def provision_agent(self, label: str) -> object:
            return object()

    examples = tuple(
        runner.ExampleSpec(name, Path("example.py"), "agent")
        for name in ("first", "second")
    )
    results: list[Any] = []

    await runner.run_group(
        runner.Plan(examples, ()),
        Provisioner(),
        object(),
        object(),
        Path.cwd(),
        results,
    )

    assert [(result.example, result.detail) for result in results] == [
        ("first", "startup: first failed"),
        ("second", "startup: second failed"),
    ]


@pytest.mark.asyncio
async def test_group_cleanup_failure_is_reported(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def stop(running: Any) -> None:
        if running.spec.id == "second":
            raise RuntimeError("termination failed")

    monkeypatch.setattr(runner, "stop_example", stop)
    running = {
        name: SimpleNamespace(spec=SimpleNamespace(id=name))
        for name in ("first", "second")
    }
    results: list[Any] = []

    await runner.stop_examples(running, results)

    assert [result.__dict__ for result in results] == [
        {
            "scenario": "cleanup",
            "example": "second",
            "status": "fail",
            "detail": "termination failed",
        }
    ]


def test_scorecard_is_written_even_when_the_run_fails(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    plan_repo: Path,
    write_plan: Callable[[dict[str, Any]], Path],
) -> None:
    """Outcomes already earned survive a crash in the observer or cleanup."""
    scorecard = tmp_path / "scorecard.json"

    async def crash(plan_: Any, repo: Path, keep: bool, results: list[Any]) -> None:
        results.append(runner.Result("independent", "one", "pass", "step 1"))
        raise RuntimeError("observer died")

    monkeypatch.setattr(runner, "run_live", crash)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runner.py",
            str(write_plan(plan())),
            "--repo",
            str(plan_repo),
            "--json-out",
            str(scorecard),
        ],
    )

    with pytest.raises(RuntimeError, match="observer died"):
        runner.main()

    assert json.loads(scorecard.read_text(encoding="utf-8")) == [
        {
            "scenario": "independent",
            "example": "one",
            "status": "pass",
            "detail": "step 1",
        }
    ]
