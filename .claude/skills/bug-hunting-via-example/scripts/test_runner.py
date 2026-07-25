from __future__ import annotations

import asyncio
import importlib.util
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


@pytest.fixture
def runner() -> ModuleType:
    path = Path(__file__).with_name("runner.py")
    spec = importlib.util.spec_from_file_location("bug_hunting_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class WaitingProcess:
    returncode = None

    async def wait(self) -> int:
        await asyncio.Event().wait()
        return 0


class Replies:
    def assert_contains_any(self, *expected: str) -> None:
        assert expected


class Calls:
    def __len__(self) -> int:
        return 1

    def assert_fired(self, tool: str) -> None:
        assert tool == "band_store_memory"


@pytest.mark.asyncio
async def test_each_step_uses_its_own_tool_boundary(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_reads: list[dict[str, Any]] = []
    sent_messages: list[Any] = []
    server_times = (
        "2001-01-01T00:00:00.100000Z",
        "2001-01-01T00:00:00.200000Z",
    )

    class Messages:
        def snapshot(self) -> list[Any]:
            return []

    class Capture:
        messages = Messages()

        async def wait_for_processed(self, message_id: str, agent_id: str) -> None:
            return None

        async def wait_for_reply(
            self, message_id: str, agent_id: str, *, since: list[Any]
        ) -> Replies:
            return Replies()

        async def tool_calls(self, **kwargs: Any) -> Calls:
            tool_reads.append(kwargs)
            return Calls()

    capture = Capture()

    @asynccontextmanager
    async def reply_capture(*args: Any, **kwargs: Any) -> Any:
        yield capture

    import tests.e2e.baseline.toolkit.capture as capture_module

    monkeypatch.setattr(capture_module, "reply_capture", reply_capture)

    class UserOps:
        async def send_message(self, *args: Any, **kwargs: Any) -> str:
            message_id = f"message-{len(sent_messages) + 1}"
            sent_messages.append(
                SimpleNamespace(
                    id=message_id,
                    inserted_at=server_times[len(sent_messages)],
                )
            )
            return message_id

        async def list_messages(self, *args: Any, **kwargs: Any) -> list[Any]:
            return list(sent_messages)

    class Resources:
        user_ops = UserOps()

        async def provision_room(self, **kwargs: Any) -> str:
            return "room-id"

    spec = runner.ExampleSpec(
        id="example",
        path=Path("example.py"),
        config_key="agent",
        steps=(
            runner.Step("silent", barrier="processed"),
            runner.Step(
                "memory",
                tools=("band_store_memory",),
                tool_calls_at_least=1,
            ),
        ),
    )
    running = SimpleNamespace(
        spec=spec,
        agent=SimpleNamespace(id="agent-id", name="agent"),
        process=WaitingProcess(),
    )
    results: list[Any] = []

    await runner.exercise_steps(
        running,
        Resources(),
        object(),
        SimpleNamespace(e2e_timeout=1),
        "independent",
        results,
    )

    assert len(results) == 2
    assert len(tool_reads) == 1
    assert tool_reads[0]["include_memory"] is True
    assert tool_reads[0]["since"] == datetime(
        2001, 1, 1, 0, 0, 0, 200000, tzinfo=timezone.utc
    )


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

    class Resources:
        async def provision_agent(self, label: str) -> object:
            return object()

    examples = tuple(
        runner.ExampleSpec(name, Path("example.py"), "agent")
        for name in ("first", "second")
    )
    results: list[Any] = []

    await runner.run_group(
        runner.Plan(examples, ()),
        Resources(),
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


def test_harness_endpoints_cannot_be_overridden(runner: ModuleType) -> None:
    spec = runner.ExampleSpec(
        "example",
        Path("example.py"),
        "agent",
        environment=(("BAND_REST_URL", "https://production.invalid"),),
        unset_env=("BAND_WS_URL",),
    )
    with pytest.raises(
        ValueError,
        match="BAND_REST_URL, BAND_WS_URL",
    ):
        runner.validate_environment_ownership(spec)

    settings = SimpleNamespace(
        endpoints=SimpleNamespace(
            rest_url="https://test.invalid",
            ws_url="wss://test.invalid/socket",
        )
    )
    environment = runner.example_environment(spec, Path("/repo"), "/tmp/run", settings)
    assert environment["BAND_REST_URL"] == "https://test.invalid"
    assert environment["BAND_WS_URL"] == "wss://test.invalid/socket"


def test_child_log_is_private(runner: ModuleType) -> None:
    spec = runner.ExampleSpec("example", Path("example.py"), "agent")
    with runner.child_log(spec) as (artifact, log_file):
        log_file.write(b"private diagnostic\n")
        log_file.flush()
        assert artifact.path.stat().st_mode & 0o777 == 0o600
        assert artifact.path.read_bytes() == b"private diagnostic\n"
    assert not artifact.path.exists()


@pytest.mark.asyncio
async def test_startup_failure_preserves_child_diagnostic(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ExitedProcess:
        returncode = 2

        async def wait(self) -> int:
            return self.returncode

    async def create_process(*args: Any, **kwargs: Any) -> ExitedProcess:
        kwargs["stdout"].write(b"startup diagnostic\n")
        kwargs["stdout"].flush()
        return ExitedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    spec = runner.ExampleSpec("example", Path("example.py"), "agent")
    agent = SimpleNamespace(id="agent-id", api_key="private-key")
    settings = SimpleNamespace(
        endpoints=SimpleNamespace(
            rest_url="https://test.invalid",
            ws_url="wss://test.invalid/socket",
        )
    )

    with pytest.raises(RuntimeError, match="exited during startup") as failure:
        await runner.start_example(spec, agent, Path.cwd(), settings)

    log_path = Path(str(failure.value).partition("child log: ")[2])
    try:
        assert log_path.stat().st_mode & 0o777 == 0o600
        assert log_path.read_text(encoding="utf-8") == "startup diagnostic\n"
    finally:
        log_path.unlink(missing_ok=True)
